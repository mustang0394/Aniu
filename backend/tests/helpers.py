"""统一测试工具（多账户方案阶段 0）。

所有测试必须使用临时数据库，不得污染项目 data 目录。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))


def reset_test_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, db_name: str = "test.db") -> None:
    """将全局引擎指向临时 SQLite 并重新初始化数据库。

    用法（配合 fixture）：:

        monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / db_name))
        reset_test_database(monkeypatch, tmp_path)
    """
    from app.core.config import get_settings
    from app.db import database as database_module
    from app.db.database import init_db
    from app.services.trading_calendar_service import trading_calendar_service

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / db_name))
    monkeypatch.setattr(trading_calendar_service, "ensure_months", lambda keys: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    init_db()
    reset_service_global_state()
    return database_module


def teardown_test_database() -> None:
    from app.core.config import get_settings
    from app.db import database as database_module

    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()


def reset_service_global_state() -> None:
    from app.services.aniu_service import aniu_service

    aniu_service._account_overview_cache = {}
    aniu_service._account_overview_cache_expires_at = {}


def session_scope():
    from app.db.database import session_scope as _session_scope

    return _session_scope()


def create_account(db, **overrides: Any):
    """创建 TradingAccount，返回 ORM 实例（未提交由调用方提交）。"""
    from app.db.models import TradingAccount

    defaults: dict[str, Any] = {
        "name": "测试账户",
        "slug": f"test-{len(overrides)}",
        "enabled": True,
        "archived": False,
        "sort_order": 0,
        "mx_api_key": "test-mx-key",
        "account_llm_enabled": False,
        "system_prompt": "你是测试证券分析师。",
        "analyst_prompt": "测试分析提示词。",
        "market_query": "上证指数今天走势",
        "news_query": "今天A股热点新闻",
        "screener_query": "今天强势股",
        "max_actions": 2,
        "trade_enabled": True,
        "allowed_markets_json": '["sh_main","sz_main"]',
        "disabled_skill_ids_json": "[]",
        "automation_context_window_tokens": 128000,
        "automation_recent_message_limit": 24,
        "automation_enable_auto_compaction": True,
        "automation_idle_summary_hours": 12,
    }
    defaults.update(overrides)
    account = TradingAccount(**defaults)
    db.add(account)
    db.flush()
    return account


def create_schedule(db, account_id: int, **overrides: Any):
    """创建归属某账户的定时任务，返回 ORM 实例（未提交）。"""
    from app.db.models import StrategySchedule

    defaults: dict[str, Any] = {
        "name": "默认任务",
        "run_type": "analysis",
        "trading_account_id": account_id,
        "interval_minutes": 30,
        "cron_expression": "*/30 * * * *",
        "task_prompt": "请根据当前市场和持仓情况生成交易决策。",
        "timeout_seconds": 1800,
        "enabled": False,
    }
    defaults.update(overrides)
    schedule = StrategySchedule(**defaults)
    db.add(schedule)
    db.flush()
    return schedule


def create_run(db, account_id: int, **overrides: Any):
    """创建归属某账户的运行记录，返回 ORM 实例（未提交）。"""
    from datetime import datetime, timezone

    from app.db.models import StrategyRun

    defaults: dict[str, Any] = {
        "trading_account_id": account_id,
        "trading_account_name_snapshot": "测试账户",
        "trigger_source": "manual",
        "run_type": "analysis",
        "status": "completed",
        "llm_config_source": "global",
        "llm_model_snapshot": "gpt-4o-mini",
        "started_at": datetime.now(timezone.utc),
        "finished_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    run = StrategyRun(**defaults)
    db.add(run)
    db.flush()
    return run

def set_default_account_key(
    db, key: str = "mx-key", *, llm: bool = True
) -> None:
    """给默认账户设置妙想 Key（并可选 LLM），供旧测试在账户化运行链下使用。"""
    from app.services.account_service import account_service

    accounts = account_service.list_accounts(db, include_archived=False)
    if not accounts:
        return
    account = accounts[0]
    account.mx_api_key = key
    if llm:
        account.account_llm_enabled = True
        account.llm_base_url = "https://example.com/v1"
        account.llm_api_key = "llm-key"
        account.llm_model = "demo-model"
    db.add(account)
