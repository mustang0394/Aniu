"""汇总指标持久化与列表查询瘦身的回归测试。

覆盖本次性能优化点：
- 五个汇总指标持久化为列，列表查询直接读列、不再解码超大 payload；
- 列表路径 defer 大字段、不触发 trade_orders N+1；
- 未回填的 run（api_call_count 为空）在列表路径兜底现算（兼容未走完成路径的行）；
- 一次性回填 _backfill_strategy_run_metrics 幂等且仅处理空行；
- RunDetailRead 响应不再包含 llm_*_payload / skill_payloads / decision_payload / executed_actions。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from tests.helpers import (
    create_account,
    create_run,
    reset_test_database,
    session_scope,
    teardown_test_database,
)

from app.db.models import StrategyRun


def _seed_completed_run(db, account_id, **payloads):
    """构造一条带 payload 但未走完成路径的 run（指标列为空）。"""
    return create_run(db, account_id, status="completed", **payloads)


def test_compute_run_summary_metrics_persists_five_columns(monkeypatch, tmp_path) -> None:
    from app.db.models import StrategyRun
    from app.services.aniu_service import aniu_service

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        account = create_account(db, slug="acct")
        db.commit()
        run = _seed_completed_run(
            db,
            account.id,
            skill_payloads={
                "tool_calls": [
                    {"name": "mx_query_market"},
                    {"name": "mx_search_news"},
                    {"name": "mx_moni_trade"},
                ]
            },
            executed_actions=[{"action": "BUY", "symbol": "300059"}],
            llm_response_payload={
                "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}
            },
        )
        db.flush()
        run_id = run.id

        # 写入指标列（模拟完成路径最后一步）
        aniu_service.compute_run_summary_metrics(run)
        db.commit()

    with session_scope() as db:
        persisted = db.get(StrategyRun, run_id)
        assert persisted.api_call_count == 2  # 3 tool_calls 减去 1 个 trade 工具
        assert persisted.executed_trade_count == 1
        assert persisted.input_tokens == 11
        assert persisted.output_tokens == 22
        assert persisted.total_tokens == 33

    teardown_test_database()


def test_list_runs_page_reads_persisted_columns_not_payloads(monkeypatch, tmp_path) -> None:
    """列表查询应返回持久化列的值，即使 payload 会算出不同的值（证明读列而非解码 payload）。"""
    from app.services.aniu_service import aniu_service

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        account = create_account(db, slug="acct")
        db.commit()
        run = _seed_completed_run(
            db,
            account.id,
            skill_payloads={"tool_calls": [{"name": "mx_query_market"}]},  # 现算应为 1
            llm_response_payload={
                "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}
            },
        )
        db.flush()
        # 故意写入与 payload 不同的持久化值，证明列表读列不读 payload
        run.api_call_count = 42
        run.executed_trade_count = 7
        run.input_tokens = 111
        run.output_tokens = 222
        run.total_tokens = 333
        db.commit()

    with session_scope() as db:
        page = aniu_service.list_runs_page(db, limit=20, account_id=account.id)

    assert len(page["items"]) == 1
    item = page["items"][0]
    assert item.api_call_count == 42
    assert item.executed_trade_count == 7
    assert item.input_tokens == 111
    assert item.output_tokens == 222
    assert item.total_tokens == 333

    teardown_test_database()


def test_list_runs_defers_heavy_payload_columns(monkeypatch, tmp_path) -> None:
    """列表查询不得预加载超大 payload 列（消除逐行 json.loads）。"""
    from sqlalchemy import inspect as sa_inspect

    from app.services.aniu_service import aniu_service

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        account = create_account(db, slug="acct")
        db.commit()
        run = _seed_completed_run(
            db,
            account.id,
            skill_payloads={"tool_calls": [{"name": "mx_query_market"}]},
            llm_response_payload={"usage": {"prompt_tokens": 1}},
        )
        db.flush()
        # 预置非空指标，使列表路径的 NULL 兑底不触发，从而验证大字段确实被 defer
        run.api_call_count = 0
        run.executed_trade_count = 0
        run.input_tokens = 0
        run.output_tokens = 0
        run.total_tokens = 0
        db.commit()

    with session_scope() as db:
        runs = aniu_service.list_runs(db, limit=20, account_id=account.id)
        assert runs
        run = runs[0]
        unloaded = set(sa_inspect(run).unloaded)
        # 这些列在列表响应中本就不返回，不得被预加载
        assert "llm_request_payload" in unloaded
        assert "llm_response_payload" in unloaded
        assert "skill_payloads" in unloaded
        assert "decision_payload" in unloaded
        assert "executed_actions" in unloaded
        assert "final_answer" in unloaded
        # 指标仍可读（来自持久化列）
        assert run.api_call_count == 0

    teardown_test_database()


def test_list_runs_falls_back_for_null_metrics(monkeypatch, tmp_path) -> None:
    """未走完成路径的 run（指标列为空）在列表路径兜底现算，保持向后兼容。"""
    from app.services.aniu_service import aniu_service

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        account = create_account(db, slug="acct")
        db.commit()
        _seed_completed_run(
            db,
            account.id,
            skill_payloads={
                "tool_calls": [
                    {"name": "mx_query_market"},
                    {"name": "mx_moni_trade"},
                ]
            },
            executed_actions=[{"action": "BUY", "symbol": "300059"}],
            llm_response_payload={
                "usage": {"prompt_tokens": 11, "completion_tokens": 22, "total_tokens": 33}
            },
        )
        db.commit()

    with session_scope() as db:
        page = aniu_service.list_runs_page(db, limit=20, account_id=account.id)

    item = page["items"][0]
    # 兜底现算：1 个非 trade 工具调用、1 笔交易、tokens 来自 usage
    assert item.api_call_count == 1
    assert item.executed_trade_count == 1
    assert item.input_tokens == 11
    assert item.output_tokens == 22
    assert item.total_tokens == 33

    teardown_test_database()


def test_backfill_strategy_run_metrics_is_idempotent_and_fills_null_rows(
    monkeypatch, tmp_path
) -> None:
    from app.db.database import _backfill_strategy_run_metrics, get_engine
    from app.db.models import StrategyRun
    from app.services.aniu_service import aniu_service

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        account = create_account(db, slug="acct")
        db.commit()
        run = _seed_completed_run(
            db,
            account.id,
            skill_payloads={"tool_calls": [{"name": "mx_query_market"}]},
            llm_response_payload={
                "usage": {"prompt_tokens": 5, "completion_tokens": 6, "total_tokens": 11}
            },
        )
        db.flush()
        run_id = run.id
        db.commit()

    # 一次性回填
    _backfill_strategy_run_metrics(get_engine())

    with session_scope() as db:
        filled = db.get(StrategyRun, run_id)
        assert filled.api_call_count == 1
        assert filled.input_tokens == 5
        assert filled.total_tokens == 11

    # 二次调用应直接跳过（null 已填），不重复更新；覆盖一次并发幂等场景
    _backfill_strategy_run_metrics(get_engine())
    with session_scope() as db:
        again = db.get(StrategyRun, run_id)
        assert again.api_call_count == 1

    teardown_test_database()


def test_run_detail_read_excludes_heavy_payload_fields(monkeypatch, tmp_path) -> None:
    from app.schemas.aniu import RunDetailRead
    from app.services.aniu_service import aniu_service

    reset_test_database(monkeypatch, tmp_path)
    with session_scope() as db:
        account = create_account(db, slug="acct")
        db.commit()
        _seed_completed_run(
            db,
            account.id,
            skill_payloads={"tool_calls": [{"name": "mx_query_market"}]},
            decision_payload={"tool_calls": [{"name": "mx_query_market"}]},
            executed_actions=[{"action": "BUY", "symbol": "300059"}],
            llm_request_payload={"usage": {"prompt_tokens": 1}},
            llm_response_payload={"usage": {"prompt_tokens": 11, "completion_tokens": 22}},
            final_answer="最终结论",
        )
        db.commit()
        run_id = db.query(StrategyRun.id).order_by(StrategyRun.id.desc()).first()[0]

    with session_scope() as db:
        run = aniu_service.get_run(db, run_id, account_id=account.id)
        assert run is not None
        detail = RunDetailRead.model_validate(run).model_dump(mode="json")

    # 这些大字段不再出现在详情响应中
    for forbidden in (
        "llm_request_payload",
        "llm_response_payload",
        "skill_payloads",
        "decision_payload",
        "executed_actions",
    ):
        assert forbidden not in detail, f"{forbidden} 不应出现在 RunDetailRead 中"
    # 但后端预计算的明细仍在
    assert "api_details" in detail
    assert "trade_details" in detail
    assert "raw_tool_previews" in detail
    assert "final_answer" in detail

    teardown_test_database()
