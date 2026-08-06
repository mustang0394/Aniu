"""Unit tests for mx_moni_trade position_pct computation.

Exercises ``MXExecutionService._compute_position_pct`` with a fake client —
no DB, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from skills.mx_core.execution import (
    _extract_balance_total_assets,
    _extract_position_quantity,
    mx_execution_service,
)


class _FakeClient:
    def __init__(self, balance=None, positions=None, raise_on=None):
        self._balance = balance
        self._positions = positions
        self._raise_on = raise_on or set()
        self.balance_calls = 0
        self.positions_calls = 0

    def get_balance(self):
        self.balance_calls += 1
        if "balance" in self._raise_on:
            raise RuntimeError("balance api down")
        return self._balance

    def get_positions(self):
        self.positions_calls += 1
        if "positions" in self._raise_on:
            raise RuntimeError("positions api down")
        return self._positions


def _balance(total: float = 100_000.0) -> dict:
    return {"data": {"totalAsset": total, "balanceActual": total}}


def _positions(*rows: dict) -> dict:
    return {"data": {"data": list(rows)}}


def _row(symbol: str, count: int) -> dict:
    return {"stockCode": symbol, "count": count, "stockName": "测试股"}


def _compute(client, *, action="BUY", symbol="600519", quantity=100,
             price=None, price_type="LIMIT"):
    return mx_execution_service._compute_position_pct(
        client=client,
        action=action,
        symbol=symbol,
        quantity=quantity,
        price=price,
        price_type=price_type,
    )


def test_buy_limit_position_pct() -> None:
    # 10 万总仓位，限价 10 元买 1000 股（1 万元）→ 10%
    client = _FakeClient(balance=_balance(total=100_000.0))
    pct = _compute(client, quantity=1000, price=10.0)
    assert pct == 10.0
    assert client.balance_calls == 1


def test_buy_market_without_price_returns_none() -> None:
    # 市价买入且未提供参考价格，金额不可靠 → 不展示比例
    client = _FakeClient(balance=_balance(total=100_000.0))
    pct = _compute(client, price=None, price_type="MARKET")
    assert pct is None
    assert client.balance_calls == 0


def test_buy_market_with_reference_price_works() -> None:
    client = _FakeClient(balance=_balance(total=100_000.0))
    pct = _compute(client, quantity=1000, price=10.0, price_type="MARKET")
    assert pct == 10.0


def test_buy_missing_balance_returns_none() -> None:
    client = _FakeClient(balance={"data": {}})
    assert _compute(client, price=10.0) is None


def test_sell_position_pct() -> None:
    # 持有 1000 股，卖出 500 股 → 50%
    client = _FakeClient(positions=_positions(_row("600519", 1000)))
    pct = _compute(client, action="SELL", quantity=500)
    assert pct == 50.0
    assert client.positions_calls == 1


def test_sell_matches_symbol_with_market_suffix() -> None:
    client = _FakeClient(positions=_positions(_row("600519.SH", 1000)))
    pct = _compute(client, action="SELL", quantity=500)
    assert pct == 50.0


def test_sell_symbol_not_held_returns_none() -> None:
    client = _FakeClient(positions=_positions(_row("000001", 1000)))
    assert _compute(client, action="SELL", quantity=500) is None


def test_sell_missing_positions_returns_none() -> None:
    client = _FakeClient(positions={"data": {}})
    assert _compute(client, action="SELL", quantity=500) is None


def test_api_failure_returns_none() -> None:
    client = _FakeClient(raise_on={"balance"})
    assert _compute(client, price=10.0) is None
    client = _FakeClient(raise_on={"positions"})
    assert _compute(client, action="SELL", quantity=500) is None


def test_extract_balance_total_assets_variants() -> None:
    assert _extract_balance_total_assets({"data": {"totalAsset": 123.0}}) == 123.0
    assert _extract_balance_total_assets({"data": {"totalAssets": 123.0}}) == 123.0
    assert _extract_balance_total_assets({"data": {"asset": 123.0}}) == 123.0
    assert _extract_balance_total_assets({"data": {"totalMoney": 123.0}}) == 123.0
    assert _extract_balance_total_assets({"data": {"result": {"totalAssets": 123.0}}}) == 123.0
    assert _extract_balance_total_assets({"data": {}}) is None
    assert _extract_balance_total_assets(None) is None
    assert _extract_balance_total_assets("junk") is None


def test_extract_position_quantity_variants() -> None:
    assert _extract_position_quantity(
        {"data": {"rows": [_row("600519", 1000)]}}, "600519"
    ) == 1000
    assert _extract_position_quantity(
        {"data": {"list": [_row("600519", 1000)]}}, "600519"
    ) == 1000
    assert _extract_position_quantity(
        {"data": [_row("600519", 1000)]}, "600519"
    ) == 1000
    assert _extract_position_quantity(
        {"data": {"data": [_row("600519.SH", 1000)]}}, "600519"
    ) == 1000
    assert _extract_position_quantity(
        {"data": {"data": [_row("600519", 1000)]}}, "000001"
    ) is None
    assert _extract_position_quantity(None, "600519") is None
