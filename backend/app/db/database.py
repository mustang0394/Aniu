from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.constants import DEFAULT_SYSTEM_PROMPT
from app.db.models import Base

_engine = None
_session_local = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{settings.sqlite_db_path.as_posix()}",
            connect_args={"check_same_thread": False},
        )
    return _engine


def get_session_local():
    global _session_local
    if _session_local is None:
        _session_local = sessionmaker(
            bind=get_engine(),
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
        )
    return _session_local


def init_db() -> None:
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    _ensure_app_settings_columns(engine)
    _ensure_trading_account_columns(engine)
    _ensure_chat_session_columns(engine)
    _ensure_chat_message_columns(engine)
    _ensure_strategy_schedule_columns(engine)
    _ensure_strategy_run_columns(engine)
    _ensure_chat_session_indexes(engine)
    _ensure_chat_message_indexes(engine)
    _ensure_strategy_run_indexes(engine)
    _ensure_strategy_schedule_indexes(engine)
    _ensure_trading_account_indexes(engine)
    _ensure_uzi_report_job_indexes(engine)
    _backfill_schedule_run_types(engine)
    _backfill_strategy_run_types(engine)
    _backfill_default_trading_account(engine)
    _backfill_uzi_mx_api_key(engine)
    _backfill_schedule_accounts(engine)
    _backfill_run_accounts(engine)
    _backfill_chat_session_accounts(engine)


def _ensure_trading_account_columns(engine) -> None:
    """新表由 create_all 创建；这里仅做幂等兜底，保证手工旧库也能补齐列。"""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "trading_accounts" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("trading_accounts")}
    statements: list[str] = []
    if "uzi_mx_api_key" in columns:
        pass  # 本表没有该列，忽略

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_trading_account_indexes(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "trading_accounts" not in table_names:
        return

    index_names = {
        index["name"]
        for index in inspector.get_indexes("trading_accounts")
        if index.get("name")
    }
    statements: list[str] = []
    if "ix_trading_accounts_slug" not in index_names:
        statements.append(
            "CREATE UNIQUE INDEX ix_trading_accounts_slug ON trading_accounts (slug)"
        )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_app_settings_columns(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "app_settings" not in table_names:
        return

    columns = {column["name"] for column in inspector.get_columns("app_settings")}
    statements: list[str] = []
    if "mx_api_key" not in columns:
        statements.append("ALTER TABLE app_settings ADD COLUMN mx_api_key VARCHAR(255)")
    if "uzi_mx_api_key" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN uzi_mx_api_key VARCHAR(512)"
        )
    if "disabled_skill_ids_json" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN disabled_skill_ids_json TEXT DEFAULT '[]'"
        )
    if "automation_session_id" not in columns:
        statements.append("ALTER TABLE app_settings ADD COLUMN automation_session_id INTEGER")
    if "automation_context_window_tokens" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN automation_context_window_tokens INTEGER DEFAULT 128000"
        )
    if "automation_recent_message_limit" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN automation_recent_message_limit INTEGER DEFAULT 24"
        )
    if "automation_enable_auto_compaction" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN automation_enable_auto_compaction BOOLEAN DEFAULT 1"
        )
    if "automation_idle_summary_hours" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN automation_idle_summary_hours INTEGER DEFAULT 12"
        )
    if "automation_context_source" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN automation_context_source VARCHAR(32) DEFAULT 'default'"
        )
    if "automation_context_detected_at" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN automation_context_detected_at DATETIME"
        )
    if "llm_enable_reasoning_content_echo" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN llm_enable_reasoning_content_echo BOOLEAN DEFAULT 0"
        )
    if "llm_reasoning_effort" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN llm_reasoning_effort VARCHAR(64)"
        )
    if "llm_max_retries" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN llm_max_retries INTEGER DEFAULT 3"
        )
    if "tg_bot_token" not in columns:
        statements.append("ALTER TABLE app_settings ADD COLUMN tg_bot_token VARCHAR(255)")
    if "tg_chat_id" not in columns:
        statements.append("ALTER TABLE app_settings ADD COLUMN tg_chat_id VARCHAR(255)")
    if "tg_notify_trade_enabled" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN tg_notify_trade_enabled BOOLEAN DEFAULT 0"
        )
    if "allowed_markets_json" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN allowed_markets_json TEXT "
            "DEFAULT '[\"sh_main\",\"sz_main\"]'"
        )
    if "capital_seal_enabled" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN capital_seal_enabled BOOLEAN DEFAULT 0"
        )
    if "capital_seal_amount" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN capital_seal_amount FLOAT DEFAULT 0"
        )
    if "app_display_name" not in columns:
        statements.append(
            "ALTER TABLE app_settings ADD COLUMN app_display_name VARCHAR(64) DEFAULT 'Aniu'"
        )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
        connection.execute(
            text(
                "UPDATE app_settings SET automation_context_window_tokens = CASE "
                "WHEN automation_context_window_tokens IS NULL OR automation_context_window_tokens = 65536 THEN 128000 "
                "ELSE automation_context_window_tokens END, "
                "automation_recent_message_limit = COALESCE(automation_recent_message_limit, 24), "
                "automation_enable_auto_compaction = COALESCE(automation_enable_auto_compaction, 1), "
                "automation_idle_summary_hours = COALESCE(automation_idle_summary_hours, 12), "
                "automation_context_source = COALESCE(NULLIF(trim(automation_context_source), ''), 'default'), "
                "tg_notify_trade_enabled = COALESCE(tg_notify_trade_enabled, 0), "
                "allowed_markets_json = COALESCE("
                "NULLIF(trim(allowed_markets_json), ''), "
                "'[\"sh_main\",\"sz_main\"]'"
                "), "
                "capital_seal_enabled = COALESCE(capital_seal_enabled, 0), "
                "capital_seal_amount = COALESCE(capital_seal_amount, 0), "
                "app_display_name = COALESCE("
                "NULLIF(trim(app_display_name), ''), "
                "'Aniu'"
                "), "
                "llm_max_retries = COALESCE(llm_max_retries, 3)"
            )
        )


def _ensure_chat_session_columns(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "chat_sessions" not in table_names:
        return

    required_columns = {
        "kind": "ALTER TABLE chat_sessions ADD COLUMN kind VARCHAR(32) DEFAULT 'user'",
        "slug": "ALTER TABLE chat_sessions ADD COLUMN slug VARCHAR(120)",
        "trading_account_id": "ALTER TABLE chat_sessions ADD COLUMN trading_account_id INTEGER",
        "archived_summary": "ALTER TABLE chat_sessions ADD COLUMN archived_summary TEXT",
        "summary_updated_at": "ALTER TABLE chat_sessions ADD COLUMN summary_updated_at DATETIME",
        "last_compacted_message_id": "ALTER TABLE chat_sessions ADD COLUMN last_compacted_message_id INTEGER",
        "last_compacted_run_id": "ALTER TABLE chat_sessions ADD COLUMN last_compacted_run_id INTEGER",
        "summary_revision": "ALTER TABLE chat_sessions ADD COLUMN summary_revision INTEGER DEFAULT 0",
    }

    with engine.begin() as connection:
        for column_name, statement in required_columns.items():
            current_columns = {
                column["name"]
                for column in inspect(connection).get_columns("chat_sessions")
            }
            if column_name in current_columns:
                continue
            connection.execute(text(statement))
        connection.execute(
            text(
                "UPDATE chat_sessions SET kind = COALESCE(NULLIF(trim(kind), ''), 'user'), "
                "summary_revision = COALESCE(summary_revision, 0)"
            )
        )


def _ensure_chat_message_columns(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "chat_messages" not in table_names:
        return

    required_columns = {
        "source": "ALTER TABLE chat_messages ADD COLUMN source VARCHAR(32)",
        "run_id": "ALTER TABLE chat_messages ADD COLUMN run_id INTEGER",
        "message_kind": "ALTER TABLE chat_messages ADD COLUMN message_kind VARCHAR(32)",
        "meta_payload": "ALTER TABLE chat_messages ADD COLUMN meta_payload JSON",
    }

    with engine.begin() as connection:
        for column_name, statement in required_columns.items():
            current_columns = {
                column["name"]
                for column in inspect(connection).get_columns("chat_messages")
            }
            if column_name in current_columns:
                continue
            connection.execute(text(statement))


def _ensure_strategy_schedule_columns(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "strategy_schedules" not in table_names:
        return

    required_columns = {
        "run_type": "ALTER TABLE strategy_schedules ADD COLUMN run_type VARCHAR(32) DEFAULT 'analysis'",
        "trading_account_id": "ALTER TABLE strategy_schedules ADD COLUMN trading_account_id INTEGER",
        "lease_token": "ALTER TABLE strategy_schedules ADD COLUMN lease_token VARCHAR(64)",
        "lease_until": "ALTER TABLE strategy_schedules ADD COLUMN lease_until DATETIME",        "cron_expression": "ALTER TABLE strategy_schedules ADD COLUMN cron_expression VARCHAR(64)",
        "task_prompt": "ALTER TABLE strategy_schedules ADD COLUMN task_prompt TEXT",
        "timeout_seconds": "ALTER TABLE strategy_schedules ADD COLUMN timeout_seconds INTEGER DEFAULT 1800",
        "retry_count": "ALTER TABLE strategy_schedules ADD COLUMN retry_count INTEGER DEFAULT 0",
        "retry_after_at": "ALTER TABLE strategy_schedules ADD COLUMN retry_after_at DATETIME",
    }

    with engine.begin() as connection:
        for column_name, statement in required_columns.items():
            current_columns = {
                column["name"]
                for column in inspect(connection).get_columns("strategy_schedules")
            }
            if column_name in current_columns:
                continue
            connection.execute(text(statement))


def _backfill_schedule_run_types(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "strategy_schedules" not in table_names:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE strategy_schedules SET run_type = 'trade' "
                "WHERE name LIKE '上午运行%' OR name LIKE '下午运行%'"
            )
        )
        connection.execute(
            text(
                "UPDATE strategy_schedules SET run_type = 'analysis' "
                "WHERE run_type IS NULL OR trim(run_type) = '' OR name IN ('盘前分析', '午间复盘', '收盘分析', '默认任务')"
            )
        )


def _backfill_strategy_run_types(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "strategy_runs" not in table_names:
        return

    db_path = Path(get_settings().sqlite_db_path)
    if not db_path.exists():
        return

    import json
    import sqlite3

    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        runs = connection.execute(
            "SELECT id, run_type, schedule_name, executed_actions, skill_payloads, decision_payload FROM strategy_runs"
        ).fetchall()
        trade_order_counts = {
            int(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT run_id, COUNT(*) FROM trade_orders GROUP BY run_id"
            ).fetchall()
        }

        for row in runs:
            schedule_name = str(row["schedule_name"] or "").strip()
            stored_run_type = str(row["run_type"] or "").strip()
            inferred = "analysis"

            if schedule_name.startswith("上午运行") or schedule_name.startswith("下午运行"):
                inferred = "trade"
            elif schedule_name in {"盘前分析", "午间复盘", "收盘分析"}:
                inferred = "analysis"
            elif trade_order_counts.get(int(row["id"]), 0) > 0:
                inferred = "trade"
            else:
                executed_actions = []
                if row["executed_actions"]:
                    try:
                        parsed_actions = json.loads(row["executed_actions"])
                        if isinstance(parsed_actions, list):
                            executed_actions = [item for item in parsed_actions if isinstance(item, dict)]
                    except Exception:
                        executed_actions = []

                if any(str(item.get("action") or "").upper() in {"BUY", "SELL", "CANCEL"} for item in executed_actions):
                    inferred = "trade"
                else:
                    tool_calls: list[dict[str, object]] = []
                    for payload_key in ("skill_payloads", "decision_payload"):
                        raw_payload = row[payload_key]
                        if not raw_payload:
                            continue
                        try:
                            parsed_payload = json.loads(raw_payload)
                        except Exception:
                            continue
                        if not isinstance(parsed_payload, dict):
                            continue
                        payload_tool_calls = parsed_payload.get("tool_calls")
                        if isinstance(payload_tool_calls, list):
                            tool_calls = [item for item in payload_tool_calls if isinstance(item, dict)]
                            if tool_calls:
                                break

                    if any(str(item.get("name") or "") in {"mx_moni_trade", "mx_moni_cancel"} for item in tool_calls):
                        inferred = "trade"
                    elif stored_run_type in {"analysis", "trade"}:
                        inferred = stored_run_type

            connection.execute(
                "UPDATE strategy_runs SET run_type = ? WHERE id = ?",
                (inferred, int(row["id"])),
            )

        connection.commit()
    finally:
        connection.close()


def _ensure_strategy_run_columns(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "strategy_runs" not in table_names:
        return

    required_columns = {
        "final_answer": "ALTER TABLE strategy_runs ADD COLUMN final_answer TEXT",
        "trading_account_id": "ALTER TABLE strategy_runs ADD COLUMN trading_account_id INTEGER",
        "trading_account_name_snapshot": "ALTER TABLE strategy_runs ADD COLUMN trading_account_name_snapshot VARCHAR(64)",
        "llm_config_source": "ALTER TABLE strategy_runs ADD COLUMN llm_config_source VARCHAR(16)",
        "llm_model_snapshot": "ALTER TABLE strategy_runs ADD COLUMN llm_model_snapshot VARCHAR(128)",
        "run_type": "ALTER TABLE strategy_runs ADD COLUMN run_type VARCHAR(32) DEFAULT 'analysis'",
        "schedule_name": "ALTER TABLE strategy_runs ADD COLUMN schedule_name VARCHAR(64)",
        "schedule_id": "ALTER TABLE strategy_runs ADD COLUMN schedule_id INTEGER",
        "chat_session_id": "ALTER TABLE strategy_runs ADD COLUMN chat_session_id INTEGER",
        "prompt_message_id": "ALTER TABLE strategy_runs ADD COLUMN prompt_message_id INTEGER",
        "response_message_id": "ALTER TABLE strategy_runs ADD COLUMN response_message_id INTEGER",
        "context_summary_version": "ALTER TABLE strategy_runs ADD COLUMN context_summary_version INTEGER",
        "context_tokens_estimate": "ALTER TABLE strategy_runs ADD COLUMN context_tokens_estimate INTEGER",
    }

    with engine.begin() as connection:
        for column_name, statement in required_columns.items():
            current_columns = {
                column["name"]
                for column in inspect(connection).get_columns("strategy_runs")
            }
            if column_name in current_columns:
                continue
            connection.execute(text(statement))


def _ensure_strategy_run_indexes(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "strategy_runs" not in table_names:
        return

    index_names = {
        index["name"]
        for index in inspector.get_indexes("strategy_runs")
        if index.get("name")
    }

    statements: list[str] = []
    if "ix_strategy_runs_started_at" not in index_names:
        statements.append(
            "CREATE INDEX ix_strategy_runs_started_at ON strategy_runs (started_at)"
        )
    if "ix_strategy_runs_chat_session_id" not in index_names:
        statements.append(
            "CREATE INDEX ix_strategy_runs_chat_session_id ON strategy_runs (chat_session_id)"
        )
    if "ix_strategy_runs_schedule_id" not in index_names:
        statements.append(
            "CREATE INDEX ix_strategy_runs_schedule_id ON strategy_runs (schedule_id)"
        )
    if "ix_strategy_runs_trading_account_id" not in index_names:
        statements.append(
            "CREATE INDEX ix_strategy_runs_trading_account_id ON strategy_runs (trading_account_id)"
        )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_strategy_schedule_indexes(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "strategy_schedules" not in table_names:
        return

    index_names = {
        index["name"]
        for index in inspector.get_indexes("strategy_schedules")
        if index.get("name")
    }

    statements: list[str] = []
    if "ix_strategy_schedules_trading_account_id" not in index_names:
        statements.append(
            "CREATE INDEX ix_strategy_schedules_trading_account_id "
            "ON strategy_schedules (trading_account_id)"
        )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _backfill_default_trading_account(engine) -> None:
    """幂等创建 slug=default 的默认账户，并从旧 AppSettings 复制交易字段。"""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "trading_accounts" not in table_names or "app_settings" not in table_names:
        return

    with engine.begin() as connection:
        existing = connection.execute(
            text("SELECT id FROM trading_accounts WHERE slug = 'default' LIMIT 1")
        ).fetchone()
        if existing is not None:
            return

        settings_row = connection.execute(
            text(
                "SELECT * FROM app_settings "
                "ORDER BY id ASC LIMIT 1"
            )
        ).mappings().fetchone()

        def _value(key: str, default: Any = None):
            if settings_row is None or key not in settings_row.keys():
                return default
            value = settings_row[key]
            return default if value is None else value

        connection.execute(
            text(
                "INSERT INTO trading_accounts ("
                "name, slug, enabled, archived, sort_order, "
                "mx_api_key, "
                "account_llm_enabled, llm_provider_name, llm_base_url, llm_api_key, "
                "llm_model, llm_reasoning_effort, llm_max_retries, "
                "llm_enable_reasoning_content_echo, "
                "system_prompt, analyst_prompt, market_query, news_query, screener_query, "
                "max_actions, trade_enabled, allowed_markets_json, disabled_skill_ids_json, "
                "automation_session_id, automation_context_window_tokens, "
                "automation_recent_message_limit, automation_enable_auto_compaction, "
                "automation_idle_summary_hours, automation_context_source, "
                "tg_bot_token, tg_chat_id, tg_notify_trade_enabled, "
                "capital_seal_enabled, capital_seal_amount "
                ") VALUES ("
                ":name, 'default', 1, 0, 0, "
                ":mx_api_key, "
                "0, NULL, NULL, NULL, "
                "NULL, NULL, NULL, "
                "NULL, "
                ":system_prompt, :analyst_prompt, :market_query, :news_query, :screener_query, "
                ":max_actions, :trade_enabled, :allowed_markets_json, :disabled_skill_ids_json, "
                ":automation_session_id, :automation_context_window_tokens, "
                ":automation_recent_message_limit, :automation_enable_auto_compaction, "
                ":automation_idle_summary_hours, :automation_context_source, "
                ":tg_bot_token, :tg_chat_id, :tg_notify_trade_enabled, "
                ":capital_seal_enabled, :capital_seal_amount "
                ")"
            ),
            {
                "name": "默认账户",
                "mx_api_key": _value("mx_api_key"),
                "system_prompt": _value(
                    "system_prompt", DEFAULT_SYSTEM_PROMPT
                )
                or DEFAULT_SYSTEM_PROMPT,
                "analyst_prompt": _value(
                    "analyst_prompt",
                    "请结合市场数据、资讯、候选股票、持仓和资金情况做判断。当信号不明确时返回HOLD。",
                ),
                "market_query": _value("market_query", "上证指数今天走势和市场概况"),
                "news_query": _value("news_query", "今天A股市场热点新闻"),
                "screener_query": _value("screener_query", "A股今天值得关注的强势股"),
                "max_actions": _value("max_actions", 2),
                "trade_enabled": _value("trade_enabled", 1),
                "allowed_markets_json": _value(
                    "allowed_markets_json", '["sh_main","sz_main"]'
                ),
                "disabled_skill_ids_json": _value("disabled_skill_ids_json", "[]"),
                "automation_session_id": _value("automation_session_id"),
                "automation_context_window_tokens": _value(
                    "automation_context_window_tokens", 128000
                ),
                "automation_recent_message_limit": _value(
                    "automation_recent_message_limit", 24
                ),
                "automation_enable_auto_compaction": _value(
                    "automation_enable_auto_compaction", 1
                ),
                "automation_idle_summary_hours": _value(
                    "automation_idle_summary_hours", 12
                ),
                "automation_context_source": _value(
                    "automation_context_source", "default"
                ),
                "tg_bot_token": _value("tg_bot_token"),
                "tg_chat_id": _value("tg_chat_id"),
                "tg_notify_trade_enabled": _value("tg_notify_trade_enabled", 0),
                "capital_seal_enabled": _value("capital_seal_enabled", 0),
                "capital_seal_amount": _value("capital_seal_amount", 0),
            },
        )


def _backfill_uzi_mx_api_key(engine) -> None:
    """UZI Key 迁移：一旦全局明确配置，账户 Key 变化不影响 UZI。"""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "app_settings" not in table_names:
        return

    columns = {
        column["name"] for column in inspect(engine).get_columns("app_settings")
    }
    if "uzi_mx_api_key" not in columns:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE app_settings SET uzi_mx_api_key = mx_api_key "
                "WHERE (uzi_mx_api_key IS NULL OR trim(uzi_mx_api_key) = '') "
                "AND mx_api_key IS NOT NULL AND trim(mx_api_key) <> ''"
            )
        )


def _backfill_schedule_accounts(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "strategy_schedules" not in table_names or "trading_accounts" not in table_names:
        return

    columns = {
        column["name"]
        for column in inspect(engine).get_columns("strategy_schedules")
    }
    if "trading_account_id" not in columns:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE strategy_schedules SET trading_account_id = "
                "(SELECT id FROM trading_accounts WHERE slug = 'default' LIMIT 1) "
                "WHERE trading_account_id IS NULL "
                "AND EXISTS (SELECT 1 FROM trading_accounts WHERE slug = 'default')"
            )
        )


def _backfill_run_accounts(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "strategy_runs" not in table_names or "trading_accounts" not in table_names:
        return

    columns = {column["name"] for column in inspect(engine).get_columns("strategy_runs")}
    if "trading_account_id" not in columns:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE strategy_runs SET trading_account_id = "
                "(SELECT id FROM trading_accounts WHERE slug = 'default' LIMIT 1), "
                "trading_account_name_snapshot = "
                "(SELECT name FROM trading_accounts WHERE slug = 'default' LIMIT 1) "
                "WHERE trading_account_id IS NULL "
                "AND EXISTS (SELECT 1 FROM trading_accounts WHERE slug = 'default')"
            )
        )


def _backfill_chat_session_accounts(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "chat_sessions" not in table_names or "trading_accounts" not in table_names:
        return

    columns = {column["name"] for column in inspect(engine).get_columns("chat_sessions")}
    if "trading_account_id" not in columns:
        return

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE chat_sessions SET trading_account_id = "
                "(SELECT id FROM trading_accounts WHERE slug = 'default' LIMIT 1) "
                "WHERE trading_account_id IS NULL "
                "AND EXISTS (SELECT 1 FROM trading_accounts WHERE slug = 'default')"
            )
        )


def _ensure_uzi_report_job_indexes(engine) -> None:
    """UZI 报告任务表索引（文档 §9）。

    新表由 ``Base.metadata.create_all`` 创建；索引同样由 ORM 的
    ``index=True`` 声明创建，这里仅作为幂等的内联迁移兜底，
    保证旧库（若曾手工建表）也能补齐索引。
    """
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "uzi_report_jobs" not in table_names:
        return

    index_names = {
        index["name"]
        for index in inspector.get_indexes("uzi_report_jobs")
        if index.get("name")
    }

    statements: list[str] = []
    if "ix_uzi_report_jobs_ticker_normalized_created_at" not in index_names:
        statements.append(
            "CREATE INDEX ix_uzi_report_jobs_ticker_normalized_created_at "
            "ON uzi_report_jobs (ticker_normalized, created_at)"
        )
    if "ix_uzi_report_jobs_status_created_at" not in index_names:
        statements.append(
            "CREATE INDEX ix_uzi_report_jobs_status_created_at "
            "ON uzi_report_jobs (status, created_at)"
        )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_chat_session_indexes(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "chat_sessions" not in table_names:
        return

    index_names = {
        index["name"]
        for index in inspector.get_indexes("chat_sessions")
        if index.get("name")
    }

    statements: list[str] = []
    if "ix_chat_sessions_kind" not in index_names:
        statements.append("CREATE INDEX ix_chat_sessions_kind ON chat_sessions (kind)")
    if "ix_chat_sessions_slug" not in index_names:
        statements.append("CREATE INDEX ix_chat_sessions_slug ON chat_sessions (slug)")
    if "ix_chat_sessions_trading_account_id" not in index_names:
        statements.append(
            "CREATE INDEX ix_chat_sessions_trading_account_id "
            "ON chat_sessions (trading_account_id)"
        )

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def _ensure_chat_message_indexes(engine) -> None:
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "chat_messages" not in table_names:
        return

    index_names = {
        index["name"]
        for index in inspector.get_indexes("chat_messages")
        if index.get("name")
    }

    statements: list[str] = []
    if "ix_chat_messages_run_id" not in index_names:
        statements.append("CREATE INDEX ix_chat_messages_run_id ON chat_messages (run_id)")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def get_db() -> Generator[Session, None, None]:
    session = get_session_local()()
    try:
        yield session
    finally:
        session.close()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    session = get_session_local()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
