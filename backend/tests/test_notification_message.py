"""Pure-function unit tests for the Telegram trade notification message body.

Only exercises ``notification_service._build_trade_message`` — no DB, no network.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.notification_service import _build_trade_message


def _base_orders() -> list[dict]:
    return [
        {
            "action": "BUY",
            "symbol": "600519",
            "name": "贵州茅台",
            "quantity": 100,
            "price_type": "LIMIT",
            "price": 13.50,
            "reason": "一线白酒龙头，资金面转暖，短期突破20日线",
        }
    ]


def test_message_includes_app_display_name() -> None:
    msg = _build_trade_message(
        trade_orders=_base_orders(),
        run_id=123,
        trigger_source="schedule",
        schedule_name="定投任务",
        app_display_name="Aniu",
    )
    assert "【Aniu】交易执行通知" in msg


def test_message_without_app_display_name_falls_back_to_plain_title() -> None:
    msg = _build_trade_message(
        trade_orders=_base_orders(),
        run_id=1,
        trigger_source="manual",
        schedule_name=None,
        app_display_name="",
    )
    assert "【" not in msg.split("\n")[0]
    assert "交易执行通知" in msg


def test_message_includes_reason() -> None:
    reason = "一线白酒龙头，资金面转暖，短期突破20日线"
    orders = _base_orders()
    orders[0]["reason"] = reason
    msg = _build_trade_message(
        trade_orders=orders,
        run_id=1,
        trigger_source="manual",
        schedule_name=None,
        app_display_name="Aniu",
    )
    assert "💬 理由:" in msg
    assert reason in msg


def test_empty_reason_omits_reason_line() -> None:
    orders = _base_orders()
    orders[0]["reason"] = ""
    msg = _build_trade_message(
        trade_orders=orders,
        run_id=1,
        trigger_source="manual",
        schedule_name=None,
        app_display_name="",
    )
    assert "💬 理由" not in msg


def test_none_reason_omits_reason_line() -> None:
    orders = _base_orders()
    orders[0]["reason"] = None
    msg = _build_trade_message(
        trade_orders=orders,
        run_id=1,
        trigger_source="manual",
        schedule_name=None,
        app_display_name="",
    )
    assert "💬 理由" not in msg


def test_html_escaping() -> None:
    orders = _base_orders()
    orders[0]["name"] = "<b>险</b>&名"
    orders[0]["reason"] = "理由含 <script> & 标签"
    msg = _build_trade_message(
        trade_orders=orders,
        run_id=1,
        trigger_source="<man>",
        schedule_name=None,
        app_display_name="<Aniu>",
    )
    # 自由文本中的 HTML 特殊字符必须被转义
    assert "&lt;Aniu&gt;" in msg
    assert "&lt;b&gt;险&lt;/b&gt;&amp;名" in msg
    assert "&lt;script&gt;" in msg
    assert "&amp; 标签" in msg
    assert "&lt;man&gt;" in msg
    # 不应残留未转义的原始标签
    assert "<script>" not in msg
    assert "<Aniu>" not in msg
    assert "<man>" not in msg


def test_reason_truncation() -> None:
    long_reason = "理" * 300  # 远超 200
    orders = _base_orders()
    orders[0]["reason"] = long_reason
    msg = _build_trade_message(
        trade_orders=orders,
        run_id=1,
        trigger_source="manual",
        schedule_name=None,
        app_display_name="",
    )
    # 截断后以省略号结尾
    reason_line = next(
        line for line in msg.splitlines() if line.startswith("  💬 理由:")
    )
    # 200 字符理由 + 省略号（split 后的 body 带一个前导空格）
    body = reason_line.split("理由:", 1)[1].strip()
    assert body.endswith("…")
    assert len(body) <= 201  # 200 + 省略号
    # 原始超长理由不应完整出现
    assert long_reason not in msg


def test_market_price_display() -> None:
    orders = [
        {
            "action": "SELL",
            "symbol": "000001",
            "name": "平安银行",
            "quantity": 200,
            "price_type": "MARKET",
            "price": None,
            "reason": "",
        }
    ]
    msg = _build_trade_message(
        trade_orders=orders,
        run_id=1,
        trigger_source="manual",
        schedule_name=None,
        app_display_name="",
    )
    assert "市价" in msg
    assert "卖出" in msg


def test_limit_price_display() -> None:
    orders = _base_orders()
    msg = _build_trade_message(
        trade_orders=orders,
        run_id=1,
        trigger_source="manual",
        schedule_name=None,
        app_display_name="",
    )
    assert "13.50元" in msg


def test_multiple_orders_each_get_reason_line() -> None:
    orders = [
        {
            "action": "BUY",
            "symbol": "600519",
            "name": "贵州茅台",
            "quantity": 100,
            "price_type": "LIMIT",
            "price": 13.50,
            "reason": "理由A",
        },
        {
            "action": "SELL",
            "symbol": "000001",
            "name": "平安银行",
            "quantity": 200,
            "price_type": "MARKET",
            "price": None,
            "reason": "理由B",
        },
        {
            "action": "BUY",
            "symbol": "600036",
            "name": "招商银行",
            "quantity": 50,
            "price_type": "LIMIT",
            "price": 35.20,
            "reason": "",
        },
    ]
    msg = _build_trade_message(
        trade_orders=orders,
        run_id=1,
        trigger_source="manual",
        schedule_name=None,
        app_display_name="Aniu",
    )
    assert msg.count("💬 理由:") == 2
    assert "理由A" in msg
    assert "理由B" in msg
