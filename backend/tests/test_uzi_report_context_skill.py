"""UZI 报告上下文技能测试（文档 §20.4 / §14）。

覆盖：按 report_id 查询、按 ticker 查询最新 completed、非完成态不返回、
report_id 与 ticker 冲突、章节默认与选择、max_chars 裁剪、7 天过期标记、
工具只读、技能发现。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.db.database import init_db, session_scope
from app.db.models import UziReportJob
from skills.uzi_report_context.handler import Skill as UziReportContextSkill


def _reset_db(monkeypatch, tmp_path) -> None:
    from app.core.config import get_settings
    from app.db import database as database_module

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "uzi_skill.db"))
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    init_db()


def _cleanup_db() -> None:
    from app.core.config import get_settings
    from app.db import database as database_module

    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()


def _make_summary(**overrides) -> dict:
    summary = {
        "schema_version": 1,
        "ticker": "600519.SH",
        "company_name": "贵州茅台",
        "overall_score": 78.5,
        "verdict": "谨慎看多",
        "one_liner": "核心结论",
        "valuation": {
            "rating": "合理偏低",
            "target_price": 1500.0,
            "upside_pct": 5.2,
            "methods": ["DCF", "PE"],
        },
        "risks": [{"title": "需求下滑", "level": "高"}],
        "catalysts": [{"title": "提价落地", "date": "2026-09-01"}],
        "panel": {
            "bullish": 12,
            "neutral": 6,
            "bearish": 2,
            "key_disagreements": ["估值分歧"],
        },
        "qualitative": {"macro": "宏观平稳"},
        "data_gaps": {
            "coverage_pct": 92.0,
            "unresolved": 1,
            "items": ["北向持仓明细缺失"],
        },
        "sources": [{"name": "公司年报", "url": "https://example.com"}],
        "data_as_of": "2026-08-15T00:00:00",
        "generated_at": "2026-08-16T00:00:00",
        "disclaimer": "历史研究资料，不构成投资建议",
    }
    summary.update(overrides)
    return summary


def _add_report(
    *,
    ticker: str = "600519.SH",
    status: str = "completed",
    created_days_ago: int = 0,
    summary: dict | None = None,
) -> int:
    job = UziReportJob(
        ticker_input=ticker,
        ticker_normalized=ticker,
        company_name="贵州茅台",
        status=status,
        phase=status,
        progress=100 if status == "completed" else 30,
        uzi_commit="7bc779d",
        llm_model="gpt-4o-mini",
        report_rel_dir="",
        summary_json=summary if summary is not None else _make_summary(),
    )
    if created_days_ago:
        job.created_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(
            days=created_days_ago
        )
    with session_scope() as db:
        db.add(job)
        db.flush()
        job.report_rel_dir = str(job.id)
        job_id = job.id
        db.commit()
    return job_id


def _skill() -> UziReportContextSkill:
    return UziReportContextSkill()


def _invoke(skill, **arguments) -> dict:
    return skill.do_uzi_get_report_context(arguments=arguments, context={})


def test_query_by_report_id(monkeypatch, tmp_path) -> None:
    _reset_db(monkeypatch, tmp_path)
    try:
        report_id = _add_report()
        result = _invoke(_skill(), report_id=report_id)
        assert result["ok"] is True
        payload = result["result"]
        assert payload["source"] == "uzi_report"
        assert payload["report_id"] == report_id
        assert payload["ticker"] == "600519.SH"
        assert payload["company_name"] == "贵州茅台"
        assert payload["is_stale"] is False
        assert payload["llm_model"] == "gpt-4o-mini"
        assert payload["uzi_commit"] == "7bc779d"
        assert "overview" in payload["sections"]
        assert payload["sections"]["overview"]["overall_score"] == 78.5
        assert payload["truncated"] is False
        assert payload["disclaimer"]
    finally:
        _cleanup_db()


def test_query_by_ticker_latest_completed(monkeypatch, tmp_path) -> None:
    _reset_db(monkeypatch, tmp_path)
    try:
        _add_report(ticker="600519.SH", status="completed", created_days_ago=10)
        latest_id = _add_report(ticker="600519.SH", status="completed", created_days_ago=0)
        result = _invoke(_skill(), ticker="600519.SH")
        assert result["ok"] is True
        assert result["result"]["report_id"] == latest_id
    finally:
        _cleanup_db()


def test_not_return_non_completed(monkeypatch, tmp_path) -> None:
    _reset_db(monkeypatch, tmp_path)
    try:
        for status in ("queued", "stage1_running", "llm_review", "stage2_running", "failed", "cancelled"):
            _add_report(ticker=f"{status}", status=status)
        queued_id = _add_report(ticker="600519.SH", status="queued")
        result = _invoke(_skill(), ticker="600519.SH")
        assert result["ok"] is False
        assert "未找到" in result["error"]

        result_by_id = _invoke(_skill(), report_id=queued_id)
        assert result_by_id["ok"] is False
        assert "不存在或未完成" in result_by_id["error"]
    finally:
        _cleanup_db()


def test_report_id_ticker_conflict(monkeypatch, tmp_path) -> None:
    _reset_db(monkeypatch, tmp_path)
    try:
        report_id = _add_report(ticker="600519.SH")
        result = _invoke(_skill(), report_id=report_id, ticker="000001.SZ")
        assert result["ok"] is False
        assert "不一致" in result["error"]

        # 一致的 ticker 允许通过。
        ok_result = _invoke(_skill(), report_id=report_id, ticker="600519.SH")
        assert ok_result["ok"] is True

        # 同时提供 report_id 时也允许使用报告公司名作为 ticker 查询键。
        by_company = _invoke(_skill(), report_id=report_id, ticker="贵州茅台")
        assert by_company["ok"] is True
    finally:
        _cleanup_db()


def test_sections_default_and_selection(monkeypatch, tmp_path) -> None:
    _reset_db(monkeypatch, tmp_path)
    try:
        report_id = _add_report()

        default = _invoke(_skill(), report_id=report_id)
        assert set(default["result"]["sections"].keys()) == {
            "overview", "valuation", "risks", "catalysts", "panel", "data_gaps",
        }

        selected = _invoke(_skill(), report_id=report_id, sections=["sources", "qualitative"])
        assert set(selected["result"]["sections"].keys()) == {"sources", "qualitative"}
        assert selected["result"]["sections"]["sources"] == [
            {"name": "公司年报", "url": "https://example.com"}
        ]
    finally:
        _cleanup_db()


def test_max_chars_truncation(monkeypatch, tmp_path) -> None:
    _reset_db(monkeypatch, tmp_path)
    try:
        report_id = _add_report()
        # max_chars 下限是 1000，构造超大 content 让默认章节超限，触发按优先级裁剪。
        big_summary = _make_summary(
            risks=[{"title": "风险" + "很" * 400} for _ in range(5)],
            catalysts=[{"title": "催化" + "很" * 400} for _ in range(5)],
            data_gaps={"coverage_pct": 50.0, "unresolved": 3, "items": ["缺口" * 500]},
        )
        big_id = _add_report(summary=big_summary)
        result = _invoke(_skill(), report_id=big_id, max_chars=1000)
        assert result["ok"] is True
        assert result["result"]["truncated"] is True
        rendered = result["result"]["sections"]
        assert "overview" in rendered  # 最高优先级始终保留
        assert "sources" not in rendered  # 最低优先级先被丢弃

        # 正常 max_chars 不触发裁剪。
        small = _invoke(_skill(), report_id=report_id)
        assert small["result"]["truncated"] is False
    finally:
        _cleanup_db()


def test_stale_after_7_days(monkeypatch, tmp_path) -> None:
    _reset_db(monkeypatch, tmp_path)
    try:
        old_id = _add_report(created_days_ago=8)
        result = _invoke(_skill(), report_id=old_id)
        assert result["ok"] is True
        assert result["result"]["is_stale"] is True
        assert result["result"]["age_days"] == 8

        fresh_id = _add_report(created_days_ago=2)
        fresh = _invoke(_skill(), report_id=fresh_id)
        assert fresh["result"]["is_stale"] is False
        assert fresh["result"]["age_days"] == 2
    finally:
        _cleanup_db()


def test_readonly_no_write_tools() -> None:
    skill = _skill()
    tool_names = skill.tool_names()
    assert tool_names == {"uzi_get_report_context"}

    forbidden = {
        "mx_moni_trade",
        "mx_moni_cancel",
        "mx_manage_self_select",
        "write_file",
        "edit_file",
        "exec",
        "http_post",
    }
    assert not (tool_names & forbidden), f"只读技能泄漏了危险工具: {tool_names & forbidden}"

    spec = skill.tools[0]["function"]
    assert spec["name"] == "uzi_get_report_context"
    params = spec["parameters"]
    assert params["additionalProperties"] is False
    assert params["properties"]["report_id"]["minimum"] == 1
    assert params["properties"]["ticker"]["maxLength"] == 64
    assert params["properties"]["max_chars"]["default"] == 12000
    enum = params["properties"]["sections"]["items"]["enum"]
    assert set(enum) == {
        "overview", "valuation", "risks", "catalysts",
        "panel", "qualitative", "data_gaps", "sources",
    }


def test_skill_discovered(monkeypatch, tmp_path) -> None:
    _reset_db(monkeypatch, tmp_path)
    try:
        from app.skills import skill_registry

        skill_registry.reload()
        packages = skill_registry.all_packages()
        found = next((pkg for pkg in packages if pkg.id == "uzi_report_context"), None)
        assert found is not None, "skill_registry 未发现 uzi_report_context"

        for run_type in ("analysis", "trade", "chat"):
            tool_names = {
                spec["function"]["name"]
                for spec in skill_registry.build_tools(run_type=run_type)
            }
            assert "uzi_get_report_context" in tool_names, (
                f"run_type={run_type} 缺少 uzi_get_report_context"
            )
    finally:
        _cleanup_db()
