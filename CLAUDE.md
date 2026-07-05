# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Aniu is an A-share (Chinese A-stock) intelligent analysis and simulated-trading platform. A FastAPI + SQLite backend hosts an LLM agent that calls a plugin system of "skills" (tools) backed by East Money's 妙想 (Miaoxiang) OpenAPI for market data, news, screening, and simulated trade execution. A Vue 3 SPA is the UI. The whole thing ships as one Docker image: the backend serves the built frontend as static files.

The product is Chinese-language first — system prompts, tool descriptions, error messages, and UI copy are all Chinese. Match this when adding user-facing strings.

## Commands

### Backend (run from `backend/`)
```bash
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
cp .env.example .env                                    # then edit secrets
./.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend (run from `frontend/`)
```bash
npm install
npm run dev          # Vite dev server on :3003, proxies /api and /health to :8000
npm run build        # vue-tsc -b typecheck, then vite build → dist/
```

### Tests
```bash
# Backend (pytest, 122 tests)
cd backend && ./.venv/bin/pytest
# Single test
cd backend && ./.venv/bin/pytest tests/test_service_guards.py::test_moni_trade_requires_limit_price -v
```
`frontend/tests/*.test.ts` are vitest-style specs but no test runner is installed/configured in `package.json` — don't assume `npm test` works without adding vitest first.

### Docker
```bash
cp .env.docker.example .env.docker       # set APP_LOGIN_PASSWORD
docker compose pull && docker compose up -d
curl http://127.0.0.1:8000/health
```

## Architecture

### Single-image, two-process layout
- **Backend** (`backend/app/main.py` → `app.main:app`): FastAPI, all routes under `/api/aniu`, plus `/health`. In Docker, `_serve_frontend()` mounts `static/` (the Vite build output) and adds an SPA catch-all that returns `index.html` for non-`/api`/`/health` paths. In local dev this is a no-op — Vite serves the frontend and proxies `/api`+`/health` to `:8000` (see `frontend/vite.config.ts`).
- **Auth**: JWT bearer. `APP_LOGIN_PASSWORD` gates `/api/aniu/login`, which issues a token; every other route depends on `get_current_user` (`app/core/auth.py`). JWT secret is auto-generated and persisted to `data/jwt_secret.txt` if `JWT_SECRET` env is unset (so login survives container rebuilds).
- **Dockerfile** is multi-stage: `node:20` builds the frontend, `python:3.12-slim` runs the backend and `COPY`s `backend/app`, `backend/skills`, and the frontend `dist`→`static`.

### The skill plugin framework — the heart of the system
This is the one subsystem you must understand before changing agent behavior. Code lives in `backend/app/skills/` plus on-disk skill packages in `backend/skills/` (builtin, shipped in image) and `data/skill_workspace/skills/` (workspace, user-imported & deletable).

A **skill package** is a directory containing:
- `SKILL.md` — YAML frontmatter (`name`, `description`, `metadata.aniu.handler_module`, `run_types`, `role`, `capabilities`, `requires`) + a markdown SOP body fed into the prompt.
- optional `handler.py` — defines a `BaseSkill` subclass (`app/skills/base.py`) declaring `tools` (OpenAI function specs) and `do_<tool_name>` methods. The folder name is the canonical skill id.

**Layered runtime** (`app/skills/`):
- `loader.py` — `discover_skill_packages()` scans builtin then workspace dirs; **workspace wins on id collision**. Parses SKILL.md frontmatter (PyYAML if available, else a naive fallback parser) and imports the handler module.
- `catalog.py` — `SkillCatalog`: thread-safe state holding packages + the disabled-id set.
- `policy.py` — `SkillPolicy`: classifies `runtime` vs `standard` skills, orders tools (runtime first), decides what goes into the prompt (always-on SOPs vs summaries), and renders the per-skill summary line with the on-disk path to its `SKILL.md`.
- `runtime.py` — `SkillRuntime`: `build_tools(run_type)` (merged, deduped-by-name tool specs), `execute_tool(tool_name, arguments, context)` (dispatches to the owning skill's `do_<tool_name>`), `build_prompt_supplement(run_type)` (injects runtime-tool list + always-on SOPs + a summary of standard skills pointing the LLM at each `SKILL.md` path).
- `registry.py` — `SkillRegistry` facade tying the three together; `skill_registry` is the singleton used everywhere.
- `providers.py` / `context.py` — `build_skill_context()` assembles the dict passed to tool handlers: `run_type`, `app_settings`, `client` (MXClient), `mx_client_config`, `chat_context_ports` (lambdas into `aniu_service`), and `skill_runtime_paths` (workspace/builtin/chat-uploads roots).

**Two skill roles**:
- `runtime` (e.g. `builtin_utils`): system-level, **always-enabled, cannot be disabled**. Provides the generic execution base (`read_file`/`write_file`/`edit_file`/`list_dir`/`glob`/`grep`/`exec`/`web_search`/`web_fetch`/`http_get`/`http_post`) so that **document-driven skills with no Python handler** can still run — the LLM reads the `SKILL.md` SOP and uses these generic tools to execute it. Writes and `exec` are confined to `skill_workspace/`.
- `standard` (e.g. `mx_core`, `chat_context`): business skills with native tool handlers.

**Skill admin** (`app/services/skill_admin_service.py`): import from ClawHub/SkillHub/zip into the workspace dir, enable/disable (state persisted in `app_settings.disabled_skill_ids_json`), delete (workspace-only; builtins rejected), reload. On startup `main.py` lifespan calls `skill_registry.reload()` then `skill_admin_service.apply_persisted_state()`.

### Run types and tool gating — the safety boundary
Three run types: `analysis`, `trade`, `chat`. Each skill declares which it supports; `mx_core` additionally gates **per-tool** via `TOOL_PROFILES` in `backend/skills/mx_core/tool_specs.py`:
- `analysis`: market query / news / screen / positions / balance / orders / self-selects — **no trade mutations**.
- `trade` and `chat`: above **plus** `mx_moni_trade` and `mx_moni_cancel`.

This means an analysis run physically cannot call trade tools — the tools aren't in the payload. `chat` exposes trade tools but `_CHAT_CONFIRMATION_APPEND_PROMPT` forces the LLM to ask for confirmation before destructive ops.

### The agentic LLM loop (`app/services/llm_service.py`)
`LLMService` talks to any OpenAI-compatible `/v1/chat/completions` endpoint via httpx SSE streaming. `_agent_loop()`:
1. For up to `_MAX_TOOL_ITERATIONS=100`: build payload (`messages` + `tools=skill_registry.build_tools(run_type)` + `tool_choice=auto`) → `_call_llm_stream`.
2. If the response has `tool_calls`, execute each via the injected `tool_executor` (which calls `skill_registry.execute_tool`), append `tool` role messages with slimmed results, loop.
3. Else emit the final answer as a streamed chunk sequence and return `{final_answer, tool_history, responses, messages}`.
- `_call_llm_stream` retries once without `stream_options.include_usage` on HTTP 400 (some providers reject it).
- `_augment_system_prompt()` composes: base system prompt + `build_prompt_supplement(run_type)` + run-type enforcement (`_TRADE_ENFORCEMENT_PROMPT` for trade, `_CHAT_CONFIRMATION_APPEND_PROMPT` for chat). The trade prompt explicitly tells the LLM that saying "建议买入" without calling `mx_moni_trade` does nothing — only tool calls execute trades.

### Two execution entry points (`app/services/aniu_service.py` — large, the orchestration hub)
- **Scheduled / manual runs**: `execute_run()` (sync) or `start_run_async()` → background worker → `_run_body()`. `run_type` is `analysis` or `trade` (resolved from the `StrategySchedule` or the manual override; falls back to schedule-name heuristics). Creates a `StrategyRun` row, builds an `MXClient`, runs `llm_service.run_agent_with_messages()`, extracts executed trade actions from `tool_history`, persists `TradeOrder` rows, and (if `tg_notify_trade_enabled`) fires `send_telegram_trade_notification()`. Progress is published to the `event_bus` and consumed via SSE at `GET /runs/{run_id}/events`.
- **Chat**: `chat_session_service.stream_chat()` → `llm_service.chat()` with `run_type="chat"`. Persistent `ChatSession` rows with message history, attachments (`docx`/`xlsx`/`pptx`/text extracted to inline text, images sent inline), and context compaction. A separate **persistent automation session** (`automation_session_id` on `AppSettings`) carries context across scheduled runs; its history is compacted when the token estimate (`token_estimator.py`) exceeds 85% of `automation_context_window_tokens` (default 128K). See CHANGELOG entries on compaction regression fixes — the compaction logic counts only non-archived messages.

### Scheduler
`app/services/scheduler_service.py`: a daemon thread polling every `SCHEDULER_POLL_SECONDS` (default 15, clamped to ≥5) calling `aniu_service.process_due_schedule()`. Schedules are `StrategySchedule` rows (`interval_minutes` or `cron_expression`, `run_type`, `task_prompt`, `timeout_seconds`, retry state).

### Data persistence
- SQLite at `data/aniu.sqlite3` (legacy `data/aniu.db` auto-detected and reused). `app/db/database.py` `init_db()` runs `Base.metadata.create_all` **plus inline `ALTER TABLE` migrations** (`_ensure_*_columns`/`_ensure_*_indexes`) — there is **no Alembic**; adding a column means adding a new model field *and* an `_ensure_*` block.
- Module-level singleton engine; `session_scope()` context manager and `get_db()` FastAPI dependency. `expire_on_commit=False`.
- `data/` also holds `jwt_secret.txt`, `skill_workspace/`, `chat_uploads/`. `backend/app/data/trading_calendar.json` is a shipped cache (A股 trading days) used by `trading_calendar_service` to avoid first-start failure when the remote calendar API is down.

### Configuration: env vs. DB
Two distinct config layers — don't confuse them:
- **Env / `app/core/config.py` `Settings`** (pydantic-settings, `.env`): bootstrap only — `APP_LOGIN_PASSWORD`, `JWT_SECRET`, `SQLITE_DB_PATH`, `CORS_ALLOW_ORIGINS`, `SCHEDULER_POLL_SECONDS`. `get_settings()` is `@lru_cache` → in tests you must `get_settings.cache_clear()` after monkeypatching env, and reset `database._engine`/`_session_local` to None, to isolate state.
- **DB `AppSettings` row** (`app/db/models.py`): runtime config edited via the Settings UI — OpenAI base URL/key/model, MX API key, system/analyst prompts, default market/news/screener queries, `max_actions`, `trade_enabled`, Telegram bot token/chat-id/notify flag, automation context-window/compaction settings, and `disabled_skill_ids_json`. Per README, OpenAI/MX config is meant to be saved in the UI, not env. The `llm_service` and skill context read these off the `AppSettings` instance via `build_skill_context`.

### Frontend
- Views (`src/views/`): Overview, Tasks, Chat, Schedule, Settings, Login — routed by `src/router/index.ts` with a JWT-in-localStorage auth guard. Data logic lives in `src/composables/` (`useAnalysisRuns`, `useRunStream`, `usePersistentSession`, `useChatSession(s)`, `useScheduleForm`, `useSkillManager`). `src/services/api.ts` is a hand-rolled fetch wrapper; SSE parsing in `src/utils/sse.ts`. Pinia store at `src/stores/legacy.ts`. `@` → `src/` alias. `npm run build` typechecks (`vue-tsc -b`) before `vite build`; do not commit `frontend/dist/`.

## Conventions worth preserving

- **Run-type tool gating is the trade safety boundary.** Never add `mx_moni_trade`/`mx_moni_cancel` to the `analysis` profile in `TOOL_PROFILES`, and never bypass `skill_registry.build_tools(run_type=...)` when constructing LLM payloads.
- **Trade mode must call tools, not just describe intent.** The `_TRADE_ENFORCEMENT_PROMPT` and `mx_core/SKILL.md` "交易执行铁律" exist to force the LLM to actually invoke `mx_moni_trade`/`mx_moni_cancel` instead of outputting "建议买入". Preserve this enforcement when touching trade prompts.
- **Adding a DB column** = add the field on the model in `app/db/models.py` **and** add an `ALTER TABLE` statement to the matching `_ensure_*_columns` in `app/db/database.py` (no Alembic migrations).
- **Adding a skill** = create `backend/skills/<id>/` with `SKILL.md` (frontmatter `metadata.aniu.handler_module`, `run_types`) and optional `handler.py` exporting a `BaseSkill` subclass; reload picks it up. Builtin skills can't be deleted; user skills live under `data/skill_workspace/skills/`.
- **Telegram notifications are best-effort** — `notification_service` swallows all errors and only logs; never let a notification failure propagate into the run path.
- **Tests isolate SQLite via monkeypatch** of `SQLITE_DB_PATH` to a tmp path + resetting the engine/session globals + `get_settings.cache_clear()` (see `tests/test_service_guards.py` for the canonical pattern).

## CI / release
`.github/workflows/publish-image.yml`: push to `main` → publish `ghcr.io/mustang0394/aniu:latest` + SHA tag; push a `v*` tag → publish versioned image + create a GitHub Release; `workflow_dispatch` supports optional `push_image`/`custom_tag` inputs. Note the image name in the workflow (`Mustang0394/aniu`) differs from the README's example (`anacondakc/aniu`) — this repo's actual registry path is the one in the workflow.


# 环境
请使用中文回复，调试过程中请注意避免污染全局环境，所以请使用venv等
