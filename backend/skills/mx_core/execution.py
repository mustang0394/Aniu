from __future__ import annotations

from typing import Any, Callable

from skills.mx_core.client import MXClient
from skills.mx_core.capital_seal import (
    apply_seal_to_balance_payload,
    check_buy_against_virtual_cash,
    extract_virtual_cash_from_balance_payload,
    get_seal_config,
)
from skills.mx_core.markets import (
    MARKET_LABELS,
    append_market_constraint_to_query,
    filter_candidates_by_markets,
    get_allowed_markets_from_settings,
    is_buy_allowed,
    normalize_allowed_markets,
)
from skills.mx_core.parsers import extract_candidates
from skills.mx_core.tool_specs import TOOL_PROFILES, TOOL_SPECS, build_tools


ERROR_HINTS: tuple[tuple[str, str], ...] = (
    ("401", "API Key 可能错误、失效或未正确配置，请检查 MX_APIKEY。"),
    ("API密钥不存在", "API Key 可能错误、失效或未正确配置，请检查 MX_APIKEY。"),
    ("code=113", "今日调用次数可能已达上限，请前往妙想 Skills 页面获取更多调用次数。"),
    ("今日调用次数已达上限", "今日调用次数可能已达上限，请前往妙想 Skills 页面获取更多调用次数。"),
    ("Connection refused", "当前网络可能无法访问东方财富妙想接口，请检查网络或稍后重试。"),
    ("connect:", "当前网络可能无法访问东方财富妙想接口，请检查网络或稍后重试。"),
    ("未绑定模拟组合账户", "当前账户可能尚未绑定模拟组合，请先在妙想 Skills 页面创建并绑定模拟账户。"),
    ("code=404", "当前账户可能尚未绑定模拟组合，请先在妙想 Skills 页面创建并绑定模拟账户。"),
    ("No dataTable found", "本次查询没有返回可用数据表，请放宽查询条件或到东方财富妙想 AI 页面确认查询方式。"),
    ("筛选结果为空", "本次筛选没有匹配到股票，请放宽选股条件。"),
)


class MXExecutionService:
    def __init__(self) -> None:
        self._tool_specs = TOOL_SPECS
        self._handlers: dict[str, Callable[..., dict[str, Any]]] = {
            "mx_query_market": self._handle_query_market,
            "mx_search_news": self._handle_search_news,
            "mx_screen_stocks": self._handle_screen_stocks,
            "mx_get_positions": self._handle_get_positions,
            "mx_get_balance": self._handle_get_balance,
            "mx_get_orders": self._handle_get_orders,
            "mx_get_self_selects": self._handle_get_self_selects,
            "mx_manage_self_select": self._handle_manage_self_select,
            "mx_moni_trade": self._handle_moni_trade,
            "mx_moni_cancel": self._handle_moni_cancel,
        }

    def build_tools(self, run_type: str | None = None) -> list[dict[str, Any]]:
        return build_tools(run_type=run_type)

    def execute_tool(
        self,
        *,
        client: MXClient,
        app_settings: Any,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        handler = self._handlers.get(tool_name)
        if handler is None:
            return {
                "ok": False,
                "tool_name": tool_name,
                "error": f"未知工具调用: {tool_name}",
            }

        try:
            return handler(
                client=client, app_settings=app_settings, arguments=arguments
            )
        except Exception as exc:
            guidance = self._build_error_guidance(str(exc))
            return {
                "ok": False,
                "tool_name": tool_name,
                "error": f"{str(exc)}{guidance}",
            }

    def _handle_query_market(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        query = self._resolve_query(arguments, app_settings)
        result = client.query_market(query)
        return {
            "ok": True,
            "tool_name": "mx_query_market",
            "summary": f"已查询市场数据：{query}。",
            "result": result,
        }

    def _handle_search_news(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        query = self._resolve_query(arguments, app_settings)
        result = client.search_news(query)
        return {
            "ok": True,
            "tool_name": "mx_search_news",
            "summary": f"已查询资讯：{query}。",
            "result": result,
        }

    def _handle_screen_stocks(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        allowed_markets = get_allowed_markets_from_settings(app_settings)
        raw_query = self._resolve_query(arguments, app_settings)
        query = append_market_constraint_to_query(raw_query, allowed_markets)
        result = client.screen_stocks(query)
        candidates = extract_candidates(result if isinstance(result, dict) else {}, limit=50)
        kept, removed = filter_candidates_by_markets(candidates, allowed_markets)
        allowed_labels = "、".join(MARKET_LABELS[key] for key in allowed_markets)
        summary = f"已执行选股：{query}。"
        if removed:
            summary += (
                f" 按选股范围（{allowed_labels}）过滤后保留 {len(kept)} 只，"
                f"排除 {len(removed)} 只。"
            )
        return {
            "ok": True,
            "tool_name": "mx_screen_stocks",
            "summary": summary,
            "result": result,
            "allowed_markets": allowed_markets,
            "filtered_candidates": kept,
            "filtered_out_count": len(removed),
            "filtered_out_samples": removed[:10],
        }

    def _handle_get_positions(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        del app_settings, arguments
        result = client.get_positions()
        return {
            "ok": True,
            "tool_name": "mx_get_positions",
            "summary": "已查询持仓。",
            "result": result,
        }

    def _handle_get_balance(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        del arguments
        result = client.get_balance()
        enabled, seal = get_seal_config(app_settings)
        projected = apply_seal_to_balance_payload(
            result if isinstance(result, dict) else None,
            seal_amount=seal,
            enabled=enabled,
        )
        summary = "已查询账户资金。"
        if enabled and isinstance(projected, dict):
            meta = projected.get("_aniu_capital_seal") or {}
            virtual_total = meta.get("virtual_total_assets")
            virtual_cash = meta.get("virtual_cash_balance")
            parts = [f"已按资金封印（{seal:.2f} 元）投影为可操作口径"]
            if virtual_total is not None:
                parts.append(f"虚拟总资产 {float(virtual_total):.2f}")
            if virtual_cash is not None:
                parts.append(f"虚拟可用 {float(virtual_cash):.2f}")
            summary = "，".join(parts) + "。"
        return {
            "ok": True,
            "tool_name": "mx_get_balance",
            "summary": summary,
            "result": projected if projected is not None else result,
        }

    def _handle_get_orders(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        del app_settings, arguments
        result = client.get_orders()
        return {
            "ok": True,
            "tool_name": "mx_get_orders",
            "summary": "已查询委托记录。",
            "result": result,
        }

    def _handle_get_self_selects(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        del app_settings, arguments
        result = client.get_self_selects()
        return {
            "ok": True,
            "tool_name": "mx_get_self_selects",
            "summary": "已查询自选股列表。",
            "result": result,
        }

    def _handle_manage_self_select(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        query = self._resolve_query(arguments, app_settings)
        result = client.manage_self_select(query)
        return {
            "ok": True,
            "tool_name": "mx_manage_self_select",
            "summary": f"已执行自选股操作：{query}",
            "result": result,
            "executed_action": {
                "action": "MANAGE_SELF_SELECT",
                "query": query,
            },
        }

    def _handle_moni_trade(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        action = str(arguments.get("action") or "").upper()
        symbol = str(arguments.get("symbol") or "").strip()
        price_type = str(arguments.get("price_type") or "MARKET").upper()
        quantity = int(arguments.get("quantity") or 0)
        price = arguments.get("price")
        reason = str(arguments.get("reason") or "").strip()

        if action not in {"BUY", "SELL"}:
            raise RuntimeError("模拟交易工具的 action 只能是 BUY 或 SELL。")
        if not symbol:
            raise RuntimeError("模拟交易工具缺少股票代码。")
        if quantity <= 0:
            raise RuntimeError("模拟交易工具的 quantity 必须大于 0。")
        if quantity % 100 != 0:
            raise RuntimeError("A 股交易数量必须是 100 的整数倍。")
        if price_type not in {"MARKET", "LIMIT"}:
            raise RuntimeError("price_type 只能是 MARKET 或 LIMIT。")
        if price_type == "LIMIT":
            try:
                normalized_price = float(price)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("LIMIT 委托必须提供有效价格。") from exc
            if normalized_price <= 0:
                raise RuntimeError("LIMIT 委托价格必须大于 0。")
            price = normalized_price
        elif price is not None:
            try:
                price = float(price)
            except (TypeError, ValueError):
                price = None

        allowed_markets = get_allowed_markets_from_settings(app_settings)
        if action == "BUY":
            allowed, market, reject_reason = is_buy_allowed(symbol, allowed_markets)
            if not allowed:
                return {
                    "ok": False,
                    "tool_name": "mx_moni_trade",
                    "error": reject_reason
                    or "当前选股范围不允许买入该股票。",
                    "allowed_markets": normalize_allowed_markets(allowed_markets),
                    "market": market,
                }

            seal_enabled, seal_amount = get_seal_config(app_settings)
            if seal_enabled:
                virtual_cash: float | None = None
                try:
                    balance_payload = client.get_balance()
                    sealed = apply_seal_to_balance_payload(
                        balance_payload if isinstance(balance_payload, dict) else None,
                        seal_amount=seal_amount,
                        enabled=True,
                    )
                    virtual_cash = extract_virtual_cash_from_balance_payload(sealed)
                except Exception:
                    virtual_cash = None
                cash_ok, cash_error = check_buy_against_virtual_cash(
                    app_settings=app_settings,
                    virtual_cash=virtual_cash,
                    quantity=quantity,
                    price=float(price) if price is not None else None,
                    price_type=price_type,
                )
                if not cash_ok:
                    return {
                        "ok": False,
                        "tool_name": "mx_moni_trade",
                        "error": cash_error or "超出虚拟可用资金，拒绝买入。",
                        "capital_seal_amount": seal_amount,
                        "virtual_cash_balance": virtual_cash,
                    }

        result = client.trade(
            action=action,
            symbol=symbol,
            quantity=quantity,
            price_type=price_type,
            price=price,
        )
        return {
            "ok": True,
            "tool_name": "mx_moni_trade",
            "summary": f"已提交{action}委托：{symbol} {quantity} 股。",
            "result": result,
            "executed_action": {
                "symbol": symbol,
                "name": str(arguments.get("name") or "").strip(),
                "action": action,
                "quantity": quantity,
                "price_type": price_type,
                "price": price,
                "reason": reason,
            },
        }

    def _handle_moni_cancel(
        self, *, client: MXClient, app_settings: Any, arguments: dict[str, Any]
    ) -> dict[str, Any]:
        del app_settings
        cancel_type = str(arguments.get("cancel_type") or "").strip().lower()
        order_id = str(arguments.get("order_id") or "").strip() or None
        stock_code = str(arguments.get("stock_code") or "").strip() or None
        reason = str(arguments.get("reason") or "").strip()

        if cancel_type not in {"all", "order"}:
            raise RuntimeError("cancel_type 只能是 all 或 order。")
        if cancel_type == "order" and not order_id:
            raise RuntimeError("按委托编号撤单时必须提供 order_id。")

        result = client.cancel_order(
            cancel_type=cancel_type,
            order_id=order_id,
            stock_code=stock_code,
        )
        return {
            "ok": True,
            "tool_name": "mx_moni_cancel",
            "summary": "已提交撤单请求。"
            if cancel_type == "all"
            else f"已提交撤单请求：{order_id}",
            "result": result,
            "executed_action": {
                "action": "CANCEL",
                "cancel_type": cancel_type,
                "order_id": order_id,
                "stock_code": stock_code,
                "reason": reason,
            },
        }

    def _resolve_query(self, arguments: dict[str, Any], app_settings: Any) -> str:
        query = str(arguments.get("query") or "").strip()
        if query:
            return query
        fallback = str(getattr(app_settings, "task_prompt", "") or "").strip()
        if fallback:
            return fallback
        raise RuntimeError("缺少 query 参数。")

    def _build_error_guidance(self, message: str) -> str:
        text = str(message or "").strip()
        if not text:
            return ""
        for needle, hint in ERROR_HINTS:
            if needle in text:
                return f"；建议：{hint}"
        return ""


mx_execution_service = MXExecutionService()
