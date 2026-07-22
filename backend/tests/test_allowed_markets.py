from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from skills.mx_core.execution import mx_execution_service
from skills.mx_core.markets import (
    append_market_constraint_to_query,
    classify_market,
    filter_candidates_by_markets,
    is_buy_allowed,
    normalize_allowed_markets,
    normalize_symbol,
)


def test_normalize_and_classify_markets() -> None:
    assert normalize_symbol("600519.SH") == "600519"
    assert normalize_symbol("sz300750") == "300750"
    assert classify_market("600519") == "sh_main"
    assert classify_market("000001") == "sz_main"
    assert classify_market("002594") == "sz_main"
    assert classify_market("300750") == "chinext"
    assert classify_market("301234") == "chinext"
    assert classify_market("688981") == "star"
    assert classify_market("689009") == "star"
    assert classify_market("830799") == "bse"
    assert classify_market("430047") == "bse"
    assert classify_market("ABC") == "unknown"


def test_normalize_allowed_markets_defaults_and_filters() -> None:
    assert normalize_allowed_markets(None) == ["sh_main", "sz_main"]
    assert normalize_allowed_markets([]) == ["sh_main", "sz_main"]
    assert normalize_allowed_markets('["chinext","star","chinext"]') == ["chinext", "star"]
    assert normalize_allowed_markets(["bad", "bse", "sh_main"]) == ["bse", "sh_main"]


def test_is_buy_allowed_respects_universe() -> None:
    allowed, market, reason = is_buy_allowed("300750", ["sh_main", "sz_main"])
    assert allowed is False
    assert market == "chinext"
    assert reason and "创业板" in reason

    allowed, market, reason = is_buy_allowed("600519", ["sh_main", "sz_main"])
    assert allowed is True
    assert market == "sh_main"
    assert reason is None

    allowed, market, reason = is_buy_allowed("BAD", ["sh_main"])
    assert allowed is False
    assert market == "unknown"


def test_filter_candidates_and_query_hint() -> None:
    kept, removed = filter_candidates_by_markets(
        [
            {"symbol": "600519", "name": "贵州茅台"},
            {"symbol": "300750", "name": "宁德时代"},
            {"symbol": "688981", "name": "中芯国际"},
        ],
        ["sh_main", "sz_main"],
    )
    assert [item["symbol"] for item in kept] == ["600519"]
    assert {item["symbol"] for item in removed} == {"300750", "688981"}
    assert "仅限上证A股、深证A股" in append_market_constraint_to_query(
        "今日强势股", ["sh_main", "sz_main"]
    )


def test_moni_trade_rejects_buy_outside_allowed_markets() -> None:
    class _Client:
        def trade(self, **kwargs):  # noqa: ANN003
            raise AssertionError("trade should not be called for blocked BUY")

    result = mx_execution_service._handle_moni_trade(
        client=_Client(),
        app_settings=SimpleNamespace(allowed_markets=["sh_main", "sz_main"]),
        arguments={
            "action": "BUY",
            "symbol": "300750",
            "quantity": 100,
            "price_type": "MARKET",
        },
    )
    assert result["ok"] is False
    assert "创业板" in str(result.get("error") or "")


def test_moni_trade_allows_sell_outside_allowed_markets() -> None:
    class _Client:
        def trade(self, **kwargs):  # noqa: ANN003
            return {"ok": True, "echo": kwargs}

    result = mx_execution_service._handle_moni_trade(
        client=_Client(),
        app_settings=SimpleNamespace(allowed_markets=["sh_main", "sz_main"]),
        arguments={
            "action": "SELL",
            "symbol": "300750",
            "quantity": 100,
            "price_type": "MARKET",
        },
    )
    assert result["ok"] is True
    assert result["executed_action"]["action"] == "SELL"


def test_moni_trade_allows_buy_inside_allowed_markets() -> None:
    class _Client:
        def trade(self, **kwargs):  # noqa: ANN003
            return {"ok": True, "echo": kwargs}

    result = mx_execution_service._handle_moni_trade(
        client=_Client(),
        app_settings=SimpleNamespace(allowed_markets=["sh_main", "sz_main"]),
        arguments={
            "action": "BUY",
            "symbol": "600519.SH",
            "quantity": 100,
            "price_type": "MARKET",
        },
    )
    assert result["ok"] is True
    assert result["executed_action"]["symbol"] == "600519.SH"


def test_settings_allowed_markets_roundtrip(monkeypatch, tmp_path) -> None:
    from app.core.config import get_settings
    from app.db import database as database_module
    from app.db.database import init_db, session_scope
    from app.schemas.aniu import AppSettingsRead, AppSettingsUpdate
    from app.services.aniu_service import aniu_service
    from app.services.trading_calendar_service import trading_calendar_service

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "markets.db"))
    monkeypatch.setattr(trading_calendar_service, "ensure_years", lambda years: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    init_db()

    with session_scope() as db:
        settings = aniu_service.get_or_create_settings(db)
        read_default = AppSettingsRead.model_validate(settings)
        assert read_default.allowed_markets == ["sh_main", "sz_main"]

        updated = aniu_service.update_settings(
            db,
            AppSettingsUpdate(
                system_prompt=settings.system_prompt,
                allowed_markets=["sh_main", "chinext", "star"],
            ),
        )
        assert updated.allowed_markets_json
        read_updated = AppSettingsRead.model_validate(updated)
        assert read_updated.allowed_markets == ["sh_main", "chinext", "star"]

    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()
