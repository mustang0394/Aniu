"""Lightweight Telegram Bot notification service."""
from __future__ import annotations

import html
import logging

import httpx

logger = logging.getLogger(__name__)

_TG_API_BASE = "https://api.telegram.org"
_TIMEOUT_SECONDS = 10

# 单条下单理由的最大展示长度，超出截断并加省略号，避免多笔交易时
# Telegram 单条消息（上限 4096 字符）被极端长的理由撑爆。
_REASON_MAX_CHARS = 200


def send_telegram_trade_notification(
    *,
    bot_token: str,
    chat_id: str,
    trade_orders: list[dict],
    run_id: int,
    trigger_source: str,
    schedule_name: str | None = None,
    app_display_name: str = "",
) -> None:
    """Send a Telegram notification for executed trade orders.

    Failure is logged but never propagated to the caller.
    """
    if not bot_token or not chat_id:
        return

    text = _build_trade_message(
        trade_orders=trade_orders,
        run_id=run_id,
        trigger_source=trigger_source,
        schedule_name=schedule_name,
        app_display_name=app_display_name,
    )

    url = f"{_TG_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        with httpx.Client(timeout=_TIMEOUT_SECONDS) as client:
            response = client.post(url, json=payload)
            response.raise_for_status()
        logger.info("Telegram notification sent: run_id=%s", run_id)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Telegram notification HTTP error: run_id=%s, status=%s, body=%s",
            run_id,
            exc.response.status_code,
            exc.response.text[:200],
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "Telegram notification network error: run_id=%s, error=%s",
            run_id,
            exc,
        )


def _truncate_reason(reason: str) -> str:
    """截断下单理由到 _REASON_MAX_CHARS 字符，超出追加省略号。"""
    if len(reason) <= _REASON_MAX_CHARS:
        return reason
    return reason[:_REASON_MAX_CHARS] + "…"


def _build_trade_message(
    *,
    trade_orders: list[dict],
    run_id: int,
    trigger_source: str,
    schedule_name: str | None,
    app_display_name: str = "",
) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")

    action_emoji = {"BUY": "\U0001f7e2", "SELL": "\U0001f534"}
    action_text = {"BUY": "买入", "SELL": "卖出"}

    display_name = (app_display_name or "").strip()
    if display_name:
        title = f"\U0001f4c8 <b>【{html.escape(display_name)}】交易执行通知</b>"
    else:
        title = "\U0001f4c8 <b>交易执行通知</b>"

    lines = [
        title,
        f"\u23f0 {now}",
    ]
    if schedule_name:
        lines.append(f"\U0001f4cb 任务: {html.escape(str(schedule_name))}")
    lines.append(
        f"\U0001f3af 来源: {html.escape(str(trigger_source))} | 运行 #{run_id}"
    )
    lines.append("")

    for order in trade_orders:
        action = str(order.get("action") or "").upper()
        emoji = action_emoji.get(action, "")
        label = action_text.get(action, action)
        symbol = str(order.get("symbol") or "?")
        name = order.get("name") or ""
        quantity = order.get("quantity") or 0
        price_type = order.get("price_type") or "MARKET"
        price = order.get("price")
        reason = str(order.get("reason") or "").strip()

        name_part = f" ({html.escape(str(name))})" if name else ""
        if price_type == "MARKET":
            price_part = " 市价"
        elif price is not None:
            price_part = f" {price:.2f}元"
        else:
            price_part = ""

        lines.append(
            f"{emoji} <b>{label}</b> {html.escape(symbol)}{name_part}"
            f" x{quantity}{price_part}"
        )

        if reason:
            lines.append(f"  \U0001f4ac 理由: {html.escape(_truncate_reason(reason))}")

    return "\n".join(lines)
