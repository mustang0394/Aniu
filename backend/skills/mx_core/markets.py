"""A-share market classification and buy-universe helpers."""
from __future__ import annotations

import json
from typing import Any, Iterable, Literal

MarketKey = Literal["sh_main", "sz_main", "chinext", "star", "bse"]

MARKET_KEYS: tuple[MarketKey, ...] = (
    "sh_main",
    "sz_main",
    "chinext",
    "star",
    "bse",
)

MARKET_LABELS: dict[MarketKey, str] = {
    "sh_main": "上证A股",
    "sz_main": "深证A股",
    "chinext": "创业板",
    "star": "科创板",
    "bse": "北交所",
}

DEFAULT_ALLOWED_MARKETS: list[MarketKey] = ["sh_main", "sz_main"]

DEFAULT_ALLOWED_MARKETS_JSON = json.dumps(
    DEFAULT_ALLOWED_MARKETS,
    ensure_ascii=False,
    separators=(",", ":"),
)

_MARKET_KEY_SET = set(MARKET_KEYS)


def normalize_symbol(symbol: str | None) -> str:
    """Normalize stock codes like ``600519.SH`` / ``sz300750`` to 6-digit form when possible."""
    text = str(symbol or "").strip().upper()
    if not text:
        return ""

    for sep in (".", ":"):
        if sep in text:
            text = text.split(sep, 1)[0].strip()

    for prefix in ("SH", "SZ", "BJ", "SS"):
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix) :]
            break

    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return digits or text


def classify_market(symbol: str | None) -> MarketKey | Literal["unknown"]:
    code = normalize_symbol(symbol)
    if len(code) != 6 or not code.isdigit():
        return "unknown"

    if code.startswith(("688", "689")):
        return "star"
    if code.startswith(("300", "301")):
        return "chinext"
    if code.startswith(("8", "4")):
        return "bse"
    if code.startswith("60"):
        return "sh_main"
    if code.startswith(("000", "001", "002", "003")):
        return "sz_main"
    return "unknown"


def market_label(market: str | None) -> str:
    key = str(market or "").strip()
    if key in MARKET_LABELS:
        return MARKET_LABELS[key]  # type: ignore[index]
    return "未知市场"


def normalize_allowed_markets(
    value: Any,
    *,
    default: Iterable[str] | None = None,
) -> list[MarketKey]:
    fallback = list(default) if default is not None else list(DEFAULT_ALLOWED_MARKETS)
    raw_items: list[Any]
    if value is None:
        raw_items = list(fallback)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raw_items = list(fallback)
        else:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = [part.strip() for part in text.split(",") if part.strip()]
            if isinstance(parsed, list):
                raw_items = parsed
            else:
                raw_items = list(fallback)
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = list(fallback)

    ordered: list[MarketKey] = []
    seen: set[str] = set()
    for item in raw_items:
        key = str(item or "").strip()
        if key not in _MARKET_KEY_SET or key in seen:
            continue
        seen.add(key)
        ordered.append(key)  # type: ignore[arg-type]

    if not ordered:
        ordered = [key for key in fallback if key in _MARKET_KEY_SET]  # type: ignore[misc]
    if not ordered:
        ordered = list(DEFAULT_ALLOWED_MARKETS)
    return ordered


def parse_allowed_markets_json(raw: str | None) -> list[MarketKey]:
    return normalize_allowed_markets(raw)


def dumps_allowed_markets(markets: Iterable[str]) -> str:
    normalized = normalize_allowed_markets(list(markets))
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))


def get_allowed_markets_from_settings(app_settings: Any) -> list[MarketKey]:
    if app_settings is None:
        return list(DEFAULT_ALLOWED_MARKETS)

    direct = getattr(app_settings, "allowed_markets", None)
    if isinstance(direct, (list, tuple, set)) and direct:
        return normalize_allowed_markets(direct)

    raw = getattr(app_settings, "allowed_markets_json", None)
    if raw is not None:
        return parse_allowed_markets_json(str(raw))

    return list(DEFAULT_ALLOWED_MARKETS)


def is_buy_allowed(
    symbol: str | None,
    allowed_markets: Iterable[str] | None,
) -> tuple[bool, MarketKey | Literal["unknown"], str | None]:
    allowed = normalize_allowed_markets(allowed_markets)
    market = classify_market(symbol)
    code = normalize_symbol(symbol) or str(symbol or "").strip() or "--"

    if market == "unknown":
        return (
            False,
            market,
            f"股票 {code} 无法识别所属市场，当前选股范围不允许买入未知代码。",
        )
    if market not in allowed:
        allowed_text = "、".join(MARKET_LABELS[key] for key in allowed)
        return (
            False,
            market,
            (
                f"股票 {code} 属于{market_label(market)}，"
                f"不在当前选股范围内（允许：{allowed_text}）。"
            ),
        )
    return True, market, None


def filter_candidates_by_markets(
    candidates: list[dict[str, str]],
    allowed_markets: Iterable[str] | None,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    allowed = set(normalize_allowed_markets(allowed_markets))
    kept: list[dict[str, str]] = []
    removed: list[dict[str, str]] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").strip()
        market = classify_market(symbol)
        enriched = {
            "symbol": symbol,
            "name": str(item.get("name") or "").strip(),
            "market": market,
            "market_label": market_label(market),
        }
        if market != "unknown" and market in allowed:
            kept.append(enriched)
        else:
            removed.append(enriched)
    return kept, removed


def build_market_constraint_hint(allowed_markets: Iterable[str] | None) -> str:
    allowed = normalize_allowed_markets(allowed_markets)
    labels = [MARKET_LABELS[key] for key in allowed]
    denied = [MARKET_LABELS[key] for key in MARKET_KEYS if key not in set(allowed)]
    parts = [
        f"仅限{'、'.join(labels)}",
    ]
    if denied:
        parts.append(f"排除{'、'.join(denied)}")
    return "，".join(parts)


def append_market_constraint_to_query(
    query: str,
    allowed_markets: Iterable[str] | None,
) -> str:
    text = str(query or "").strip()
    hint = build_market_constraint_hint(allowed_markets)
    if not text:
        return hint
    if hint in text:
        return text
    return f"{text}（{hint}）"


def build_allowed_markets_prompt(allowed_markets: Iterable[str] | None) -> str:
    allowed = normalize_allowed_markets(allowed_markets)
    labels = "、".join(MARKET_LABELS[key] for key in allowed)
    denied = [MARKET_LABELS[key] for key in MARKET_KEYS if key not in set(allowed)]
    denied_text = "、".join(denied) if denied else "无"
    return "\n".join(
        [
            "## 选股范围约束（功能设置）",
            f"当前允许选股与买入的市场：{labels}。",
            f"禁止买入：{denied_text}。",
            "规则：",
            "1. 选股、推荐与新建仓买入必须落在允许市场内。",
            "2. 卖出、撤单不受此范围限制（便于清理历史持仓）。",
            "3. 调用 mx_screen_stocks 时请在 query 中体现上述范围；系统也会在服务端过滤与拦截。",
            "4. 若候选均不在允许范围内，应选择观望（HOLD），不要强行买入。",
        ]
    )
