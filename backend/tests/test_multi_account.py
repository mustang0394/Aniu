"""多妙想 Key、多独立模拟账户测试（实施方案 §17）。

覆盖：数据迁移、LLM 兜底、妙想 Key 隔离、Skills 隔离、调度 lease、
账户 API、密钥脱敏与跨账户访问拒绝。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.helpers import (
    create_account,
    create_run,
    create_schedule,
    reset_test_database,
    session_scope,
    teardown_test_database,
)


def _make_client(monkeypatch, tmp_path, db_name="multi.db") -> TestClient:
    from app.core.config import get_settings
    from app.core.rate_limit import _limiter
    from app.main import create_app
    from app.services.scheduler_service import scheduler_service

    monkeypatch.setenv("APP_LOGIN_PASSWORD", "release-pass")
    reset_test_database(monkeypatch, tmp_path, db_name)
    monkeypatch.setattr(scheduler_service, "start", lambda: None)
    monkeypatch.setattr(scheduler_service, "stop", lambda: None)
    _limiter._buckets.clear()
    return TestClient(create_app())


def _auth_headers(client: TestClient) -> dict[str, str]:
    response = client.post("/api/aniu/login", json={"password": "release-pass"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['token']}"}


# ── 迁移 ──────────────────────────────────────────────────────────────────


def test_init_db_creates_default_account(monkeypatch, tmp_path) -> None:
    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        from app.db.models import TradingAccount

        accounts = db.query(TradingAccount).all()
        assert len(accounts) == 1
        assert accounts[0].slug == "default"
        assert accounts[0].name == "默认账户"
        assert accounts[0].archived is False
    teardown_test_database()


def test_init_db_is_idempotent_and_default_account_singleton(monkeypatch, tmp_path) -> None:
    from app.db.database import init_db

    reset_test_database(monkeypatch, tmp_path)
    init_db()
    init_db()
    with session_scope() as db:
        from app.db.models import TradingAccount

        assert len(db.query(TradingAccount).filter(TradingAccount.slug == "default").all()) == 1
    teardown_test_database()


def test_legacy_database_upgrade_backfills_accounts_and_uzi_key(monkeypatch, tmp_path) -> None:
    """旧单账户库升级：任务/运行/会话回填默认账户，UZI Key 保留。"""
    import sqlite3

    db_path = tmp_path / "legacy.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE app_settings (
            id INTEGER PRIMARY KEY, mx_api_key VARCHAR(255), system_prompt TEXT,
            market_query VARCHAR(255), news_query VARCHAR(255), screener_query VARCHAR(255),
            max_actions INTEGER, trade_enabled BOOLEAN, allowed_markets_json TEXT,
            disabled_skill_ids_json TEXT, provider_name VARCHAR(32),
            llm_base_url VARCHAR(255), llm_api_key VARCHAR(255), llm_model VARCHAR(128),
            created_at DATETIME, updated_at DATETIME,
            capital_seal_enabled BOOLEAN, capital_seal_amount FLOAT,
            tg_notify_trade_enabled BOOLEAN, automation_context_window_tokens INTEGER,
            automation_recent_message_limit INTEGER, automation_enable_auto_compaction BOOLEAN,
            automation_idle_summary_hours INTEGER, analyst_prompt TEXT,
            llm_reasoning_effort VARCHAR(64), llm_max_retries INTEGER,
            llm_enable_reasoning_content_echo BOOLEAN, tg_bot_token VARCHAR(255),
            tg_chat_id VARCHAR(255)
        );
        CREATE TABLE strategy_schedules (
            id INTEGER PRIMARY KEY, name VARCHAR(64), run_type VARCHAR(32),
            interval_minutes INTEGER, cron_expression VARCHAR(64), task_prompt TEXT,
            timeout_seconds INTEGER, enabled BOOLEAN, retry_count INTEGER,
            last_run_at DATETIME, next_run_at DATETIME, retry_after_at DATETIME,
            created_at DATETIME, updated_at DATETIME
        );
        CREATE TABLE strategy_runs (
            id INTEGER PRIMARY KEY, trigger_source VARCHAR(32), run_type VARCHAR(32),
            status VARCHAR(32), started_at DATETIME, schedule_name VARCHAR(64),
            executed_actions TEXT, skill_payloads TEXT, decision_payload TEXT,
            created_at DATETIME, analysis_summary TEXT, final_answer TEXT,
            error_message TEXT, finished_at DATETIME, schedule_id INTEGER,
            chat_session_id INTEGER, llm_request_payload TEXT,
            llm_response_payload TEXT
        );
        CREATE TABLE chat_sessions (
            id INTEGER PRIMARY KEY, title VARCHAR(120), kind VARCHAR(32), slug VARCHAR(120),
            created_at DATETIME, updated_at DATETIME, last_message_at DATETIME,
            archived_summary TEXT, summary_revision INTEGER,
            last_compacted_message_id INTEGER, last_compacted_run_id INTEGER
        );
        CREATE TABLE chat_messages (
            id INTEGER PRIMARY KEY, session_id INTEGER, role VARCHAR(16), content TEXT
        );
        CREATE TABLE trade_orders (
            id INTEGER PRIMARY KEY, run_id INTEGER, symbol VARCHAR(16), action VARCHAR(16),
            quantity INTEGER, price_type VARCHAR(16), status VARCHAR(32),
            created_at DATETIME
        );
        CREATE TABLE uzi_report_jobs (
            id INTEGER PRIMARY KEY, ticker_input VARCHAR(64), ticker_normalized VARCHAR(32),
            status VARCHAR(32), created_at DATETIME, updated_at DATETIME
        );
        """
    )
    connection.execute(
        "INSERT INTO app_settings (mx_api_key, system_prompt) VALUES (?, ?)",
        ("legacy-mx-key", "旧系统提示词"),
    )
    connection.execute(
        "INSERT INTO strategy_schedules (name, run_type, enabled) VALUES (?, ?, 1)",
        ("盘前分析", "analysis"),
    )
    connection.execute(
        "INSERT INTO strategy_runs (trigger_source, run_type, status) VALUES ('manual', 'analysis', 'completed')"
    )
    connection.execute(
        "INSERT INTO chat_sessions (title, kind, slug) VALUES ('旧会话', 'user', NULL)"
    )
    connection.commit()
    connection.close()

    monkeypatch.setenv("SQLITE_DB_PATH", str(db_path))
    from app.core.config import get_settings
    from app.db import database as database_module

    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    database_module.init_db()

    with session_scope() as db:
        from app.db.models import (
            AppSettings,
            ChatSession,
            StrategyRun,
            StrategySchedule,
            TradingAccount,
        )

        default_account = db.query(TradingAccount).filter(TradingAccount.slug == "default").one()
        assert default_account.mx_api_key == "legacy-mx-key"
        assert default_account.system_prompt == "旧系统提示词"

        schedule = db.query(StrategySchedule).one()
        assert schedule.trading_account_id == default_account.id

        run = db.query(StrategyRun).one()
        assert run.trading_account_id == default_account.id
        assert run.trading_account_name_snapshot == "默认账户"

        chat_session = db.query(ChatSession).one()
        assert chat_session.trading_account_id == default_account.id

        settings = db.query(AppSettings).one()
        assert settings.uzi_mx_api_key == "legacy-mx-key"
        assert settings.mx_api_key == "legacy-mx-key"
    teardown_test_database()


# ── LLM 兜底（§17.2） ─────────────────────────────────────────────────────


def _global_settings(**overrides):
    defaults = dict(
        provider_name="openai-compatible",
        llm_base_url="https://global.example.com/v1",
        llm_api_key="global-key",
        llm_model="global-model",
        llm_reasoning_effort=None,
        llm_max_retries=3,
        llm_enable_reasoning_content_echo=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_resolve_llm_config_uses_account_when_complete(monkeypatch, tmp_path) -> None:
    from app.services.account_context import resolve_llm_config

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        account = create_account(
            db,
            slug="a",
            account_llm_enabled=True,
            llm_base_url="https://acct.example.com/v1",
            llm_api_key="acct-key",
            llm_model="acct-model",
            llm_reasoning_effort="high",
            llm_max_retries=5,
        )
        config = resolve_llm_config(account, _global_settings())
        assert config.source == "account"
        assert config.model == "acct-model"
        assert config.base_url == "https://acct.example.com/v1"
        assert config.max_retries == 5
        assert config.reasoning_effort == "high"
    teardown_test_database()


def test_resolve_llm_config_falls_back_to_global(monkeypatch, tmp_path) -> None:
    from app.services.account_context import resolve_llm_config

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        # 未启用账户 LLM
        account = create_account(
            db, slug="a", account_llm_enabled=False,
            llm_base_url="https://acct.example.com/v1", llm_api_key="acct-key",
            llm_model="acct-model",
        )
        config = resolve_llm_config(account, _global_settings())
        assert config.source == "global"
        assert config.model == "global-model"

        # 启用但不完整：整体回退全局（不允许半混合）
        account.account_llm_enabled = True
        account.llm_base_url = None
        config = resolve_llm_config(account, _global_settings())
        assert config.source == "global"
    teardown_test_database()


def test_resolve_llm_config_raises_when_both_unavailable(monkeypatch, tmp_path) -> None:
    from app.services.account_context import resolve_llm_config

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        account = create_account(db, slug="a", account_llm_enabled=True)
        with pytest.raises(RuntimeError, match="账户和全局大模型配置均不可用"):
            resolve_llm_config(account, _global_settings(llm_base_url=None, llm_api_key=None))
    teardown_test_database()


# ── 妙想 Key 隔离与账户总览（§17.3） ─────────────────────────────────────


def test_account_overview_uses_account_mx_key(monkeypatch, tmp_path) -> None:
    from app.services import aniu_service as aniu_service_module

    reset_test_database(monkeypatch, tmp_path)
    used_keys: list[str] = []

    class FakeMXClient:
        def __init__(self, api_key=None, base_url=None):
            used_keys.append(api_key)
            self._api_key = api_key

        def get_balance(self):
            return {"data": {"totalAsset": 111, "balanceActual": 60, "stockMarketValue": 51}}

        def get_positions(self):
            return {"data": {"rows": []}}

        def get_orders(self):
            return {"data": {"rows": []}}

        def close(self):
            pass

    monkeypatch.setattr(aniu_service_module, "MXClient", FakeMXClient)
    with session_scope() as db:
        account_a = create_account(db, slug="acct-a", name="A账户", mx_api_key="key-a")
        account_b = create_account(db, slug="acct-b", name="B账户", mx_api_key="key-b")
        db.commit()
        a_id, b_id = account_a.id, account_b.id

    overview_a = aniu_service_module.aniu_service.get_account_overview(
        a_id, force_refresh=True
    )
    overview_b = aniu_service_module.aniu_service.get_account_overview(
        b_id, force_refresh=True
    )
    assert used_keys == ["key-a", "key-b"]
    assert overview_a["total_assets"] == 111
    assert overview_b["total_assets"] == 111
    teardown_test_database()


def test_account_overview_caches_are_per_account(monkeypatch, tmp_path) -> None:
    from app.services import aniu_service as aniu_service_module

    reset_test_database(monkeypatch, tmp_path)

    class FakeMXClient:
        def __init__(self, api_key=None, base_url=None):
            self._api_key = api_key

        def get_balance(self):
            total = 1000 if self._api_key == "key-a" else 2000
            return {"data": {"totalAsset": total, "balanceActual": total}}

        def get_positions(self):
            return {"data": {"rows": []}}

        def get_orders(self):
            return {"data": {"rows": []}}

        def close(self):
            pass

    monkeypatch.setattr(aniu_service_module, "MXClient", FakeMXClient)
    with session_scope() as db:
        account_a = create_account(db, slug="acct-a", mx_api_key="key-a")
        account_b = create_account(db, slug="acct-b", mx_api_key="key-b")
        db.commit()
        a_id, b_id = account_a.id, account_b.id

    aniu_service_module.aniu_service.get_account_overview(a_id, force_refresh=True)
    cached_a = aniu_service_module.aniu_service.get_account_overview(a_id)
    cached_b = aniu_service_module.aniu_service.get_account_overview(b_id)
    assert cached_a["total_assets"] == 1000
    assert cached_b["total_assets"] == 2000
    teardown_test_database()


def test_recent_account_snapshot_is_per_account(monkeypatch, tmp_path) -> None:
    from app.services import aniu_service as aniu_service_module

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        account_a = create_account(db, slug="acct-a")
        account_b = create_account(db, slug="acct-b")
        create_run(
            db,
            account_a.id,
            skill_payloads={
                "tool_calls": [
                    {
                        "name": "mx_get_balance",
                        "result": {"ok": True, "result": {"data": {"totalAsset": 111}}},
                    }
                ]
            },
        )
        create_run(
            db,
            account_b.id,
            skill_payloads={
                "tool_calls": [
                    {
                        "name": "mx_get_balance",
                        "result": {"ok": True, "result": {"data": {"totalAsset": 222}}},
                    }
                ]
            },
        )
        db.commit()
        a_id, b_id = account_a.id, account_b.id

    with session_scope() as db:
        balance_a, _, _ = aniu_service_module.aniu_service._get_recent_account_snapshot(
            db, account_id=a_id
        )
        balance_b, _, _ = aniu_service_module.aniu_service._get_recent_account_snapshot(
            db, account_id=b_id
        )
    assert balance_a == {"data": {"totalAsset": 111}}
    assert balance_b == {"data": {"totalAsset": 222}}
    teardown_test_database()


# ── Skills 隔离（§17.4） ──────────────────────────────────────────────────


def test_account_disabled_skills_do_not_leak_across_accounts(monkeypatch, tmp_path) -> None:
    from app.skills.registry import skill_registry

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        account_a = create_account(
            db,
            slug="acct-a",
            disabled_skill_ids_json=json.dumps(["uzi_report_context"]),
            account_llm_enabled=True,
            llm_base_url="https://acct.example.com/v1",
            llm_api_key="acct-key",
            llm_model="acct-model",
        )
        account_b = create_account(
            db,
            slug="acct-b",
            disabled_skill_ids_json="[]",
            account_llm_enabled=True,
            llm_base_url="https://acct.example.com/v1",
            llm_api_key="acct-key",
            llm_model="acct-model",
        )
        db.commit()
        a_id, b_id = account_a.id, account_b.id

    from app.services.account_context import build_account_run_context
    from app.services.aniu_service import aniu_service

    with session_scope() as db:
        context_a = build_account_run_context(
            db, account_id=a_id, schedule_id=None, manual_run_type="analysis"
        )
        context_b = build_account_run_context(
            db, account_id=b_id, schedule_id=None, manual_run_type="analysis"
        )

    tools_a = skill_registry.build_tools(
        run_type="analysis", disabled_skill_ids=context_a.disabled_skill_ids
    )
    tools_b = skill_registry.build_tools(
        run_type="analysis", disabled_skill_ids=context_b.disabled_skill_ids
    )
    names_a = {tool["function"]["name"] for tool in tools_a}
    names_b = {tool["function"]["name"] for tool in tools_b}

    assert "uzi_get_report_context" not in names_a
    assert "uzi_get_report_context" in names_b
    # 全局 catalog 未被运行时修改
    assert skill_registry.enabled_packages() is not None

    supplement_a = skill_registry.build_prompt_supplement(
        run_type="analysis", disabled_skill_ids=context_a.disabled_skill_ids
    )
    supplement_b = skill_registry.build_prompt_supplement(
        run_type="analysis", disabled_skill_ids=context_b.disabled_skill_ids
    )
    assert "uzi_report_context" not in supplement_a
    assert "uzi_report_context" in supplement_b
    teardown_test_database()


# ── 调度 lease（§17.5） ───────────────────────────────────────────────────


def test_schedule_lease_atomic_claim(monkeypatch, tmp_path) -> None:
    from app.services import aniu_service as aniu_service_module

    reset_test_database(monkeypatch, tmp_path)
    shanghai_tz = __import__("zoneinfo").ZoneInfo("Asia/Shanghai")
    with session_scope() as db:
        account_a = create_account(db, slug="acct-a")
        db.commit()
        a_id = account_a.id
        schedule = create_schedule(
            db,
            a_id,
            name="盘前分析",
            enabled=True,
            next_run_at=datetime(2026, 4, 13, 7, 30, tzinfo=timezone.utc),
        )
        db.commit()
        schedule_id = schedule.id

    called: list[tuple[int, str, int]] = []
    monkeypatch.setattr(
        aniu_service_module,
        "now_shanghai",
        lambda: datetime(2026, 4, 13, 15, 31, tzinfo=shanghai_tz),
    )
    monkeypatch.setattr(
        aniu_service_module.trading_calendar_service, "is_trading_day", lambda d: True
    )
    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "execute_run",
        lambda account_id=None, trigger_source="manual", schedule_id=None: called.append(
            (account_id, trigger_source, schedule_id)
        ),
    )
    aniu_service_module.aniu_service.process_due_schedule()
    assert called == [(a_id, "schedule", schedule_id)]

    # 第二次：lease 未过期，不能重复抢占
    aniu_service_module.aniu_service.process_due_schedule()
    assert len(called) == 1
    teardown_test_database()


def test_same_account_two_due_schedules_dispatches_one(monkeypatch, tmp_path) -> None:
    from app.services import aniu_service as aniu_service_module

    reset_test_database(monkeypatch, tmp_path)
    shanghai_tz = __import__("zoneinfo").ZoneInfo("Asia/Shanghai")
    with session_scope() as db:
        account_a = create_account(db, slug="acct-a")
        db.commit()
        a_id = account_a.id
        schedule_1 = create_schedule(
            db, a_id, name="盘前分析", enabled=True,
            next_run_at=datetime(2026, 4, 13, 7, 30, tzinfo=timezone.utc),
        )
        schedule_2 = create_schedule(
            db, a_id, name="午间复盘", enabled=True,
            next_run_at=datetime(2026, 4, 13, 7, 30, tzinfo=timezone.utc),
        )
        db.commit()
        s1_id, s2_id = schedule_1.id, schedule_2.id

    called: list[tuple[int, str, int]] = []
    monkeypatch.setattr(
        aniu_service_module,
        "now_shanghai",
        lambda: datetime(2026, 4, 13, 15, 31, tzinfo=shanghai_tz),
    )
    monkeypatch.setattr(
        aniu_service_module.trading_calendar_service, "is_trading_day", lambda d: True
    )
    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "execute_run",
        lambda account_id=None, trigger_source="manual", schedule_id=None: called.append(
            (account_id, trigger_source, schedule_id)
        ),
    )
    aniu_service_module.aniu_service.process_due_schedule()
    assert len(called) == 1
    assert called[0][0] == a_id
    # 另一个任务的 lease 被清理，可被下一轮抢占
    with session_scope() as db:
        from app.db.models import StrategySchedule

        remaining = db.get(StrategySchedule, s1_id if called[0][2] == s2_id else s2_id)
        assert remaining is not None
        assert remaining.lease_token is None
        assert remaining.lease_until is None
    teardown_test_database()


def test_run_lock_is_per_account(monkeypatch, tmp_path) -> None:
    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        account_a = create_account(db, slug="acct-a")
        account_b = create_account(db, slug="acct-b")
        db.commit()
        a_id, b_id = account_a.id, account_b.id

    from app.services.aniu_service import aniu_service

    lock_a = aniu_service._get_account_run_lock(a_id)
    lock_b = aniu_service._get_account_run_lock(b_id)
    assert lock_a is not lock_b
    assert lock_a.acquire(blocking=False)
    try:
        # A 账户锁住时 B 账户锁不受影响
        assert lock_b.acquire(blocking=False)
        lock_b.release()
        # A 账户第二次获取失败
        assert not aniu_service._get_account_run_lock(a_id).acquire(blocking=False)
    finally:
        lock_a.release()
    teardown_test_database()


# ── 账户 API（§17.6） ─────────────────────────────────────────────────────


def test_accounts_api_crud_and_masking(monkeypatch, tmp_path) -> None:
    client = _make_client(monkeypatch, tmp_path)
    headers = _auth_headers(client)

    # 默认账户存在
    response = client.get("/api/aniu/accounts", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["slug"] == "default"

    # 创建账户
    response = client.post(
        "/api/aniu/accounts",
        headers=headers,
        json={
            "name": "趋势账户",
            "slug": "trend",
            "mx_api_key": "super-secret-key-123",
            "account_llm_enabled": False,
            "system_prompt": "趋势交易",
            "allowed_markets": ["sh_main", "sz_main"],
        },
    )
    assert response.status_code == 201
    account = response.json()
    account_id = account["id"]
    assert account["slug"] == "trend"
    assert account["has_mx_api_key"] is True
    assert account["mx_api_key"] != "super-secret-key-123"
    assert "****" in account["mx_api_key"]
    assert account["resolved_llm_source"] == "none"

    # 更新：含 **** 的密钥保持原值
    response = client.patch(
        f"/api/aniu/accounts/{account_id}",
        headers=headers,
        json={"name": "趋势账户2", "mx_api_key": account["mx_api_key"]},
    )
    assert response.status_code == 200
    with session_scope() as db:
        from app.db.models import TradingAccount

        updated = db.get(TradingAccount, account_id)
        assert updated.mx_api_key == "super-secret-key-123"
        assert updated.name == "趋势账户2"

    # 空字符串清除密钥
    response = client.patch(
        f"/api/aniu/accounts/{account_id}",
        headers=headers,
        json={"mx_api_key": ""},
    )
    assert response.status_code == 200
    with session_scope() as db:
        from app.db.models import TradingAccount

        updated = db.get(TradingAccount, account_id)
        assert updated.mx_api_key is None

    # 归档/恢复
    response = client.post(f"/api/aniu/accounts/{account_id}/archive", headers=headers)
    assert response.status_code == 200
    assert response.json()["archived"] is True
    response = client.post(f"/api/aniu/accounts/{account_id}/restore", headers=headers)
    assert response.status_code == 200
    assert response.json()["archived"] is False
    teardown_test_database()


def test_account_schedule_api_is_scoped(monkeypatch, tmp_path) -> None:
    client = _make_client(monkeypatch, tmp_path)
    headers = _auth_headers(client)

    with session_scope() as db:
        account_a = create_account(db, slug="acct-a")
        account_b = create_account(db, slug="acct-b")
        create_schedule(db, account_a.id, name="A任务", run_type="analysis")
        create_schedule(db, account_b.id, name="B任务", run_type="trade")
        db.commit()
        a_id, b_id = account_a.id, account_b.id

    response = client.get(f"/api/aniu/accounts/{a_id}/schedule", headers=headers)
    assert response.status_code == 200
    schedules = response.json()
    assert len(schedules) == 1
    assert schedules[0]["name"] == "A任务"
    assert schedules[0]["trading_account_id"] == a_id

    response = client.get(f"/api/aniu/accounts/{b_id}/schedule", headers=headers)
    assert len(response.json()) == 1
    assert response.json()[0]["name"] == "B任务"

    # 多账户下旧全局接口返回 409
    response = client.get("/api/aniu/schedule", headers=headers)
    assert response.status_code == 409
    teardown_test_database()


def test_account_run_and_runs_api_scoping(monkeypatch, tmp_path) -> None:
    client = _make_client(monkeypatch, tmp_path)
    headers = _auth_headers(client)

    with session_scope() as db:
        account_a = create_account(db, slug="acct-a")
        account_b = create_account(db, slug="acct-b")
        create_run(db, account_a.id, status="completed")
        create_run(db, account_b.id, status="completed")
        db.commit()
        a_id, b_id = account_a.id, account_b.id
    with session_scope() as db:
        from app.db.models import StrategyRun

        run_a = db.query(StrategyRun).filter(StrategyRun.trading_account_id == a_id).first()
        run_b = db.query(StrategyRun).filter(StrategyRun.trading_account_id == b_id).first()

    response = client.get(f"/api/aniu/accounts/{a_id}/runs", headers=headers)
    assert response.status_code == 200
    runs = response.json()
    assert len(runs) == 1
    assert runs[0]["id"] == run_a.id
    assert runs[0]["trading_account_id"] == a_id

    # 跨账户访问拒绝：B 账户看不到 A 的运行
    response = client.get(f"/api/aniu/accounts/{b_id}/runs/{run_a.id}", headers=headers)
    assert response.status_code == 404

    # 正确账户可读
    response = client.get(f"/api/aniu/accounts/{a_id}/runs/{run_a.id}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == run_a.id

    # 跨账户删除拒绝
    response = client.delete(f"/api/aniu/accounts/{b_id}/runs/{run_a.id}", headers=headers)
    assert response.status_code == 404
    teardown_test_database()


def test_account_skills_api(monkeypatch, tmp_path) -> None:
    client = _make_client(monkeypatch, tmp_path)
    headers = _auth_headers(client)

    with session_scope() as db:
        account_a = create_account(db, slug="acct-a")
        account_b = create_account(db, slug="acct-b")
        db.commit()
        a_id, b_id = account_a.id, account_b.id

    # A 禁用 uzi_report_context
    response = client.put(
        f"/api/aniu/accounts/{a_id}/skills",
        headers=headers,
        json=["mx_core", "chat_context"],
    )
    assert response.status_code == 200
    payload = response.json()
    assert "uzi_report_context" not in payload["account_enabled"]
    assert "uzi_report_context" not in payload["effective_enabled"]
    assert "mx_core" in payload["effective_enabled"]

    response = client.get(f"/api/aniu/accounts/{a_id}/skills", headers=headers)
    assert response.status_code == 200
    a_status = {
        item["id"]: item for item in response.json()["global_available"]
    }
    assert a_status["uzi_report_context"]["effective_enabled"] is False
    assert a_status["uzi_report_context"]["account_disabled"] is True
    assert a_status["mx_core"]["effective_enabled"] is True

    # B 不受影响
    response = client.get(f"/api/aniu/accounts/{b_id}/skills", headers=headers)
    b_status = {
        item["id"]: item for item in response.json()["global_available"]
    }
    assert b_status["uzi_report_context"]["effective_enabled"] is True
    teardown_test_database()


def test_global_overview_aggregates(monkeypatch, tmp_path) -> None:
    from app.services import aniu_service as aniu_service_module

    client = _make_client(monkeypatch, tmp_path)
    headers = _auth_headers(client)

    class FakeMXClient:
        def __init__(self, api_key=None, base_url=None):
            self._api_key = api_key
            self._total = 80000 if api_key == "key-a" else 120000

        def get_balance(self):
            return {
                "data": {
                    "totalAsset": self._total,
                    "balanceActual": self._total,
                    "stockMarketValue": 0,
                    "initMoney": 100000,
                }
            }

        def get_positions(self):
            return {"data": {"rows": []}}

        def get_orders(self):
            return {"data": {"rows": []}}

        def close(self):
            pass

    monkeypatch.setattr(aniu_service_module, "MXClient", FakeMXClient)
    with session_scope() as db:
        from app.db.models import TradingAccount

        default_account = db.query(TradingAccount).filter(
            TradingAccount.slug == "default"
        ).one()
        db.delete(default_account)
        account_a = create_account(db, slug="acct-a", mx_api_key="key-a")
        account_b = create_account(db, slug="acct-b", mx_api_key="key-b")
        db.commit()

    response = client.get("/api/aniu/overview?force_refresh=true", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert len(payload["accounts"]) == 2
    aggregate = payload["aggregate"]
    assert aggregate["total_assets"] == 200000
    assert aggregate["initial_capital"] == 200000
    assert aggregate["total_return_ratio"] == 0.0
    assert payload["errors"] == []
    teardown_test_database()
