"""Capital seal projection: present operable funds as real − seal to the agent."""
from __future__ import annotations

import copy
from typing import Any

# Balance keys treated as account-level cash / assets that should subtract seal.
_TOTAL_ASSET_KEYS = (
    "totalAsset",
    "totalAssets",
    "asset",
    "totalMoney",
    "total_assets",
)
_CASH_KEYS = (
    "balanceActual",
    "availBalance",
    "availableBalance",
    "availableMoney",
    "cashBalance",
    "balance",
    "cash_balance",
)
_INIT_KEYS = ("initMoney", "initialCapital", "initial_capital")
# Market value / P&L keys must NOT be reduced by the seal.
_MARKET_VALUE_KEYS = (
    "marketValue",
    "stockMarketValue",
    "positionValue",
    "totalPosValue",
    "total_market_value",
)
_SEAL_META_KEY = "_aniu_capital_seal"


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_non_negative(value: float) -> float:
    return value if value > 0 else 0.0


def normalize_seal_amount(value: Any) -> float:
    amount = _as_float(value)
    if amount is None or amount < 0:
        return 0.0
    return float(amount)


def is_seal_enabled(app_settings: Any) -> bool:
    if app_settings is None:
        return False
    flag = getattr(app_settings, "capital_seal_enabled", False)
    if isinstance(flag, str):
        return flag.strip().lower() in {"1", "true", "yes", "on"}
    return bool(flag)


def get_seal_amount(app_settings: Any) -> float:
    if app_settings is None:
        return 0.0
    return normalize_seal_amount(getattr(app_settings, "capital_seal_amount", 0))


def get_seal_config(app_settings: Any) -> tuple[bool, float]:
    enabled = is_seal_enabled(app_settings)
    amount = get_seal_amount(app_settings)
    if not enabled or amount <= 0:
        return False, 0.0
    return True, amount


def _first_present(data: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        if key in data and data.get(key) is not None:
            parsed = _as_float(data.get(key))
            if parsed is not None:
                return parsed
    return None


def _subtract_keys(data: dict[str, Any], keys: tuple[str, ...], seal: float) -> None:
    for key in keys:
        if key not in data or data.get(key) is None:
            continue
        current = _as_float(data.get(key))
        if current is None:
            continue
        data[key] = _clamp_non_negative(current - seal)


def _recompute_position_pct(data: dict[str, Any]) -> None:
    total = _first_present(data, _TOTAL_ASSET_KEYS)
    market = _first_present(data, _MARKET_VALUE_KEYS)
    if total is None or total <= 0 or market is None:
        return
    ratio = market / total
    # Prefer writing the keys already present; otherwise set totalPosPct.
    if "totalPosPct" in data:
        # Upstream sometimes uses percent (0-100) and sometimes fraction (0-1).
        original = _as_float(data.get("totalPosPct"))
        if original is not None and original > 1.5:
            data["totalPosPct"] = round(ratio * 100, 6)
        else:
            data["totalPosPct"] = round(ratio, 6)
    else:
        data["totalPosPct"] = round(ratio, 6)


def _recompute_nav_like(data: dict[str, Any]) -> None:
    total = _first_present(data, _TOTAL_ASSET_KEYS)
    initial = _first_present(data, _INIT_KEYS)
    if total is None or initial is None or initial <= 0:
        if "nav" in data:
            data["nav"] = None
        return
    nav = total / initial
    if "nav" in data or True:
        data["nav"] = round(nav, 8)


def apply_seal_to_balance_payload(
    payload: dict[str, Any] | None,
    *,
    seal_amount: float,
    enabled: bool = True,
) -> dict[str, Any] | None:
    """Project a Miaoxiang balance payload to operable funds.

    Idempotent: re-applying on an already sealed payload is a no-op.
    """
    if not isinstance(payload, dict):
        return payload

    seal = normalize_seal_amount(seal_amount)
    if not enabled or seal <= 0:
        return payload

    existing_meta = payload.get(_SEAL_META_KEY)
    if isinstance(existing_meta, dict) and existing_meta.get("applied"):
        return payload

    projected = copy.deepcopy(payload)
    data = projected.get("data")
    if not isinstance(data, dict):
        # Some callers pass the inner data dict directly.
        data = projected
        inner_mode = False
    else:
        inner_mode = True

    real_total = _first_present(data, _TOTAL_ASSET_KEYS)
    real_cash = _first_present(data, _CASH_KEYS)
    real_init = _first_present(data, _INIT_KEYS)
    real_market = _first_present(data, _MARKET_VALUE_KEYS)

    _subtract_keys(data, _TOTAL_ASSET_KEYS, seal)
    _subtract_keys(data, _CASH_KEYS, seal)
    _subtract_keys(data, _INIT_KEYS, seal)

    # Nested result.totalAssets etc.
    nested = data.get("result")
    if isinstance(nested, dict):
        _subtract_keys(nested, _TOTAL_ASSET_KEYS, seal)
        _subtract_keys(nested, _CASH_KEYS, seal)
        _subtract_keys(nested, _INIT_KEYS, seal)

    _recompute_position_pct(data)
    _recompute_nav_like(data)

    virtual_total = _first_present(data, _TOTAL_ASSET_KEYS)
    virtual_cash = _first_present(data, _CASH_KEYS)
    seal_breached = bool(
        real_cash is not None and real_cash + 1e-9 < seal
    )

    meta = {
        "applied": True,
        "seal_amount": seal,
        "real_total_assets": real_total,
        "real_cash_balance": real_cash,
        "real_initial_capital": real_init,
        "real_market_value": real_market,
        "virtual_total_assets": virtual_total,
        "virtual_cash_balance": virtual_cash,
        "seal_breached": seal_breached,
    }
    if inner_mode:
        projected[_SEAL_META_KEY] = meta
    else:
        projected[_SEAL_META_KEY] = meta
    return projected


def apply_seal_to_overview(
    overview: dict[str, Any] | None,
    *,
    seal_amount: float,
    enabled: bool = True,
) -> dict[str, Any] | None:
    """Project a normalized account overview (UI / chat tools)."""
    if not isinstance(overview, dict):
        return overview

    seal = normalize_seal_amount(seal_amount)
    if not enabled or seal <= 0:
        return overview

    existing_meta = overview.get("capital_seal")
    if isinstance(existing_meta, dict) and existing_meta.get("applied"):
        return overview

    projected = dict(overview)
    real_total = _as_float(projected.get("total_assets"))
    real_cash = _as_float(projected.get("cash_balance"))
    real_init = _as_float(projected.get("initial_capital"))
    market = _as_float(projected.get("total_market_value"))
    daily_profit = _as_float(projected.get("daily_profit"))

    virtual_total = (
        _clamp_non_negative(real_total - seal) if real_total is not None else None
    )
    virtual_cash = (
        _clamp_non_negative(real_cash - seal) if real_cash is not None else None
    )
    virtual_init = (
        _clamp_non_negative(real_init - seal) if real_init is not None else None
    )

    if virtual_total is not None:
        projected["total_assets"] = virtual_total
    if virtual_cash is not None:
        projected["cash_balance"] = virtual_cash
    if virtual_init is not None:
        projected["initial_capital"] = virtual_init

    # Position ratio: market / virtual total (fraction 0-1 used by frontend).
    if market is not None and virtual_total is not None and virtual_total > 0:
        projected["total_position_ratio"] = market / virtual_total
    elif market is not None and virtual_total is not None and virtual_total <= 0:
        projected["total_position_ratio"] = None

    # NAV / return vs virtual initial.
    if virtual_total is not None and virtual_init is not None and virtual_init > 0:
        nav = virtual_total / virtual_init
        projected["nav"] = nav
        projected["total_return_ratio"] = nav - 1
    else:
        # Avoid leaking real-account NAV when seal is on.
        if "nav" in projected:
            projected["nav"] = None
        if (
            virtual_total is not None
            and virtual_init not in (None, 0)
            and virtual_init is not None
            and virtual_init > 0
        ):
            projected["total_return_ratio"] = virtual_total / virtual_init - 1

    if daily_profit is not None and virtual_total is not None:
        previous = virtual_total - daily_profit
        if previous > 0:
            projected["daily_return_ratio"] = daily_profit / previous
        else:
            projected["daily_return_ratio"] = None

    seal_breached = bool(real_cash is not None and real_cash + 1e-9 < seal)
    projected["capital_seal"] = {
        "applied": True,
        "enabled": True,
        "seal_amount": seal,
        "real_total_assets": real_total,
        "real_cash_balance": real_cash,
        "real_initial_capital": real_init,
        "virtual_total_assets": virtual_total,
        "virtual_cash_balance": virtual_cash,
        "seal_breached": seal_breached,
    }
    return projected


def estimate_buy_notional(
    *,
    quantity: int,
    price: float | None,
    price_type: str,
) -> float | None:
    """Estimate buy notional. MARKET without price cannot be sized reliably."""
    qty = int(quantity or 0)
    if qty <= 0:
        return None
    normalized_type = str(price_type or "MARKET").upper()
    if price is None:
        if normalized_type == "MARKET":
            return None
        return None
    try:
        px = float(price)
    except (TypeError, ValueError):
        return None
    if px <= 0:
        return None
    return qty * px


def check_buy_against_virtual_cash(
    *,
    app_settings: Any,
    virtual_cash: float | None,
    quantity: int,
    price: float | None,
    price_type: str,
) -> tuple[bool, str | None]:
    enabled, seal = get_seal_config(app_settings)
    if not enabled:
        return True, None

    if virtual_cash is None:
        return (
            False,
            "已启用资金封印，但无法读取虚拟可用资金，拒绝买入。请先查询资金后再下单。",
        )

    notional = estimate_buy_notional(
        quantity=quantity, price=price, price_type=price_type
    )
    if notional is None:
        # MARKET without price: still block if virtual cash is zero; otherwise
        # require LIMIT so we can enforce the seal.
        if virtual_cash <= 0:
            return (
                False,
                (
                    f"已启用资金封印（封印 {seal:.2f} 元），当前虚拟可用资金为 0，"
                    "无法买入。"
                ),
            )
        if str(price_type or "").upper() == "MARKET":
            return (
                False,
                (
                    "已启用资金封印时，市价买入必须提供参考 price 以便校验额度，"
                    "或改用 LIMIT 限价委托。"
                ),
            )
        return False, "无法估算买入金额，拒绝下单。"

    # Small buffer for fees / rounding (0.1%).
    budget = float(virtual_cash) * 0.999
    if notional > budget + 1e-6:
        return (
            False,
            (
                f"买入金额约 {notional:.2f} 元，超过虚拟可用资金 "
                f"{virtual_cash:.2f} 元（真实可用已扣除封印 {seal:.2f} 元）。"
            ),
        )
    return True, None


def extract_virtual_cash_from_balance_payload(
    payload: dict[str, Any] | None,
) -> float | None:
    if not isinstance(payload, dict):
        return None
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return None
    return _first_present(data, _CASH_KEYS)


def build_capital_seal_prompt(app_settings: Any) -> str:
    enabled, seal = get_seal_config(app_settings)
    if not enabled:
        return ""
    return "\n".join(
        [
            "## 资金封印约束（功能设置）",
            f"当前已启用资金封印：封印金额 {seal:.2f} 元。",
            "规则：",
            "1. 工具返回的总资产、可用资金、初始资金、仓位比例与收益率，均已按"
            "「真实值 − 封印」处理后的可操作口径给出。",
            "2. 持仓明细的数量/市值/盈亏是真实持仓，不要把持仓市值再减封印。",
            "3. 买入金额不得超过工具返回的虚拟可用资金；卖出/撤单不受封印限制。",
            "4. 必须以本轮工具返回的资金数据为准，忽略历史对话中过期的资金数字。",
            "5. 盈利会使真实总资产上升，从而推高可操作本金；不要假设本金永远固定。",
        ]
    )
