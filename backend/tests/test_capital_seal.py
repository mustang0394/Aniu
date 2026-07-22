from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[1]))

from skills.mx_core.capital_seal import (
    apply_seal_to_balance_payload,
    apply_seal_to_overview,
    check_buy_against_virtual_cash,
    extract_virtual_cash_from_balance_payload,
)
from skills.mx_core.execution import mx_execution_service


def _raw_balance(
    *,
    total: float = 1_000_000,
    cash: float = 1_000_000,
    market: float = 0,
    init: float = 1_000_000,
) -> dict:
    return {
        "data": {
            "totalAsset": total,
            "balanceActual": cash,
            "availBalance": cash,
            "marketValue": market,
            "initMoney": init,
            "totalPosPct": (market / total * 100) if total else 0,
            "nav": total / init if init else 1,
        }
    }


def test_apply_seal_empty_account_projects_operable_capital() -> None:
    sealed = apply_seal_to_balance_payload(
        _raw_balance(), seal_amount=900_000, enabled=True
    )
    assert sealed is not None
    data = sealed["data"]
    assert data["totalAsset"] == 100_000
    assert data["balanceActual"] == 100_000
    assert data["availBalance"] == 100_000
    assert data["marketValue"] == 0
    assert data["initMoney"] == 100_000
    meta = sealed["_aniu_capital_seal"]
    assert meta["applied"] is True
    assert meta["real_total_assets"] == 1_000_000
    assert meta["virtual_total_assets"] == 100_000


def test_apply_seal_profit_raises_virtual_total() -> None:
    sealed = apply_seal_to_balance_payload(
        _raw_balance(total=1_020_000, cash=900_000, market=120_000),
        seal_amount=900_000,
        enabled=True,
    )
    data = sealed["data"]
    assert data["totalAsset"] == 120_000
    assert data["balanceActual"] == 0
    assert data["marketValue"] == 120_000
    # Position ratio recomputed on virtual total (~100%).
    assert abs(float(data["totalPosPct"]) - 100.0) < 1e-6 or abs(
        float(data["totalPosPct"]) - 1.0
    ) < 1e-6


def test_apply_seal_is_idempotent() -> None:
    first = apply_seal_to_balance_payload(
        _raw_balance(), seal_amount=900_000, enabled=True
    )
    second = apply_seal_to_balance_payload(first, seal_amount=900_000, enabled=True)
    assert second["data"]["totalAsset"] == 100_000
    assert second["_aniu_capital_seal"]["real_total_assets"] == 1_000_000


def test_overview_seal_recomputes_ratios() -> None:
    overview = {
        "total_assets": 1_000_000,
        "cash_balance": 1_000_000,
        "initial_capital": 1_000_000,
        "total_market_value": 0,
        "total_position_ratio": 0,
        "nav": 1.0,
        "total_return_ratio": 0.0,
        "daily_profit": 0.0,
        "daily_return_ratio": 0.0,
    }
    sealed = apply_seal_to_overview(overview, seal_amount=900_000, enabled=True)
    assert sealed["total_assets"] == 100_000
    assert sealed["cash_balance"] == 100_000
    assert sealed["initial_capital"] == 100_000
    assert sealed["capital_seal"]["applied"] is True


def test_mx_get_balance_handler_seals_before_model() -> None:
    class _Client:
        def get_balance(self) -> dict:
            return _raw_balance()

    result = mx_execution_service._handle_get_balance(
        client=_Client(),
        app_settings=SimpleNamespace(
            capital_seal_enabled=True, capital_seal_amount=900_000
        ),
        arguments={},
    )
    assert result["ok"] is True
    data = result["result"]["data"]
    assert data["totalAsset"] == 100_000
    assert "资金封印" in result["summary"]


def test_buy_rejected_when_exceeds_virtual_cash() -> None:
    class _Client:
        def get_balance(self) -> dict:
            return _raw_balance(total=1_000_000, cash=1_000_000, market=0)

        def trade(self, **kwargs):  # noqa: ANN003
            raise AssertionError("trade must not be called")

    result = mx_execution_service._handle_moni_trade(
        client=_Client(),
        app_settings=SimpleNamespace(
            capital_seal_enabled=True,
            capital_seal_amount=900_000,
            allowed_markets=["sh_main", "sz_main"],
        ),
        arguments={
            "action": "BUY",
            "symbol": "600519",
            "quantity": 100,
            "price_type": "LIMIT",
            "price": 2000,  # 200_000 > virtual 100_000
        },
    )
    assert result["ok"] is False
    assert "虚拟可用" in str(result.get("error") or "")


def test_buy_allowed_within_virtual_cash() -> None:
    class _Client:
        def get_balance(self) -> dict:
            return _raw_balance()

        def trade(self, **kwargs):  # noqa: ANN003
            return {"ok": True, "echo": kwargs}

    result = mx_execution_service._handle_moni_trade(
        client=_Client(),
        app_settings=SimpleNamespace(
            capital_seal_enabled=True,
            capital_seal_amount=900_000,
            allowed_markets=["sh_main", "sz_main"],
        ),
        arguments={
            "action": "BUY",
            "symbol": "600519",
            "quantity": 100,
            "price_type": "LIMIT",
            "price": 100,  # 10_000 < virtual 100_000
        },
    )
    assert result["ok"] is True


def test_market_buy_without_price_blocked_when_seal_on() -> None:
    class _Client:
        def get_balance(self) -> dict:
            return _raw_balance()

        def trade(self, **kwargs):  # noqa: ANN003
            raise AssertionError("trade must not be called")

    result = mx_execution_service._handle_moni_trade(
        client=_Client(),
        app_settings=SimpleNamespace(
            capital_seal_enabled=True,
            capital_seal_amount=900_000,
            allowed_markets=["sh_main"],
        ),
        arguments={
            "action": "BUY",
            "symbol": "600519",
            "quantity": 100,
            "price_type": "MARKET",
        },
    )
    assert result["ok"] is False
    assert "市价" in str(result.get("error") or "") or "price" in str(
        result.get("error") or ""
    ).lower()


def test_check_buy_helpers() -> None:
    ok, err = check_buy_against_virtual_cash(
        app_settings=SimpleNamespace(
            capital_seal_enabled=True, capital_seal_amount=900_000
        ),
        virtual_cash=100_000,
        quantity=100,
        price=50,
        price_type="LIMIT",
    )
    assert ok is True
    assert err is None

    sealed = apply_seal_to_balance_payload(
        _raw_balance(), seal_amount=900_000, enabled=True
    )
    assert extract_virtual_cash_from_balance_payload(sealed) == 100_000


def test_settings_capital_seal_roundtrip(monkeypatch, tmp_path) -> None:
    from app.core.config import get_settings
    from app.db import database as database_module
    from app.db.database import init_db, session_scope
    from app.schemas.aniu import AppSettingsRead, AppSettingsUpdate
    from app.services.aniu_service import aniu_service
    from app.services.trading_calendar_service import trading_calendar_service

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "seal.db"))
    monkeypatch.setattr(trading_calendar_service, "ensure_years", lambda years: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    init_db()

    with session_scope() as db:
        settings = aniu_service.get_or_create_settings(db)
        updated = aniu_service.update_settings(
            db,
            AppSettingsUpdate(
                system_prompt=settings.system_prompt,
                capital_seal_enabled=True,
                capital_seal_amount=900_000,
            ),
        )
        read = AppSettingsRead.model_validate(updated)
        assert read.capital_seal_enabled is True
        assert read.capital_seal_amount == 900_000

    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()
