"""agent_analysis 上游 schema 兼容性测试（阻断项4）。

用上游 UZI-Skill 的 lib.agent_analysis_validator 校验 AniU 组装输出的
agent_analysis.json，确保不会触发上游 error 级回退到脚本骨架。
上游源码以行内副本方式提供（不可 import 时跳过），见 /tmp/uzi-research。
"""
from __future__ import annotations

from pathlib import Path
import copy
import sys
from types import SimpleNamespace

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.uzi_llm_orchestrator import UziLlmOrchestrator
from tests.uzi_test_helpers import valid_uzi_synthesis

# 上游 validator 直接内联在测试中（与上游 lib/agent_analysis_validator.py 一致），
# 避免对 /tmp 研究目录的运行时依赖。
def _upstream_validate(agent_analysis: dict) -> list:
    """与上游 lib/agent_analysis_validator.validate 语义一致的错误级检查。"""
    issues: list = []
    if not isinstance(agent_analysis, dict):
        return [{"severity": "error", "field": "(root)", "message": "必须为 dict"}]
    if agent_analysis.get("agent_reviewed") is not True:
        issues.append({"severity": "warning", "field": "agent_reviewed", "message": "缺标记"})
    dc = agent_analysis.get("dim_commentary")
    if dc is not None:
        if not isinstance(dc, dict):
            issues.append({"severity": "error", "field": "dim_commentary", "message": "必须是 dict"})
        else:
            for k, v in dc.items():
                if not isinstance(v, str):
                    issues.append({"severity": "error", "field": f"dim_commentary.{k}", "message": "评语必须为字符串"})
    pi = agent_analysis.get("panel_insights")
    if pi is not None and not (isinstance(pi, str) and len(pi.strip()) >= 1):
        issues.append({"severity": "warning", "field": "panel_insights", "message": "应为字符串"})
    gdo = agent_analysis.get("great_divide_override")
    if gdo is not None and not isinstance(gdo, dict):
        issues.append({"severity": "error", "field": "great_divide_override", "message": "必须是 dict"})
    no = agent_analysis.get("narrative_override")
    if no is not None and not isinstance(no, dict):
        issues.append({"severity": "error", "field": "narrative_override", "message": "必须是 dict"})
    dga = agent_analysis.get("data_gap_acknowledged")
    if dga is not None and not isinstance(dga, dict):
        issues.append({"severity": "error", "field": "data_gap_acknowledged", "message": "必须是 dict"})
    qdd = agent_analysis.get("qualitative_deep_dive")
    if qdd is not None:
        if not isinstance(qdd, dict):
            issues.append({"severity": "error", "field": "qualitative_deep_dive", "message": "必须是 dict"})
        else:
            for dim_k, dim_v in qdd.items():
                if not isinstance(dim_v, dict):
                    issues.append({"severity": "error", "field": f"qualitative_deep_dive.{dim_k}", "message": "维度内容必须是 dict"})
                elif isinstance(dim_v.get("evidence"), list) is False and dim_v.get("evidence") is not None:
                    issues.append({"severity": "error", "field": f"qualitative_deep_dive.{dim_k}.evidence", "message": "evidence 必须是 list"})
    return issues


def _assemble(stage1_panel=None, synthesis=None) -> dict:
    stage1 = {
        "manifest": {"ticker_normalized": "600519.SH", "company_name": "贵州茅台", "data_as_of": "2026-08-16T00:00:00"},
        "panel": stage1_panel or {"signal_distribution": {"bullish": 18, "neutral": 21, "bearish": 12, "skip": 0}},
        "data_gaps": {"coverage_pct": 92, "unresolved": 1, "items": []},
    }
    # 面板子任务结果含 per_investor_override（review 问题5）
    panel_results = {
        f"panel_{c}": {"topic": "t", "per_investor_override": {
            f"{c}_inv1": {"signal": "bullish", "score": 80,
                          "headline": f"{c} 看多", "reasoning": f"{c} 估值低。",
                          "comment": f"{c} 看多", "verdict": "买入"}}}
        for c in "abcd"
    }
    qual_results = {f"qual_{c}": {"topic": "t", "evidence": [{"source": "s", "finding": "f"}], "conclusion": "结论。"} for c in "abc"}
    return UziLlmOrchestrator._assemble_agent_analysis(
        stage1=stage1,
        panel_results=panel_results,
        qualitative_results=qual_results,
        consistency={"ok": True},
        synthesis=synthesis if synthesis is not None else valid_uzi_synthesis(),
        app_settings=SimpleNamespace(llm_model="m"),
    )


def test_assembled_analysis_passes_anuu_validation() -> None:
    aa = _assemble()
    ok, errors = UziLlmOrchestrator._validate_agent_analysis(aa)
    assert ok, errors


def test_assembled_analysis_passes_upstream_error_rules() -> None:
    """对齐上游 validator 的错误级规则：panel_insights 字符串、data_gap_acknowledged dict、narrative_override dict。"""
    aa = _assemble()
    errors = [i for i in _upstream_validate(aa) if i["severity"] == "error"]
    assert errors == [], f"上游 error 级 issues: {errors}"
    # 关键字段类型断言。
    assert isinstance(aa["panel_insights"], str)
    assert isinstance(aa["data_gap_acknowledged"], dict)
    assert isinstance(aa["narrative_override"], dict)
    assert aa["_aniu_meta"]["panel_subtasks"]


def test_old_schema_would_have_failed_upstream() -> None:
    """回归验证：旧 schema（panel_insights=dict、data_gap_acknowledged=bool）会在上游报 error。"""
    bad = _assemble()
    bad["panel_insights"] = {"summary": "ok"}
    bad["data_gap_acknowledged"] = True
    bad["narrative_override"] = "叙事"
    errors = [i for i in _upstream_validate(bad) if i["severity"] == "error"]
    # panel_insights 在旧版是 dict → 上游（字符串要求）不报 error 但 Aniu 校验应拒绝；
    # data_gap_acknowledged=True → 上游 error；narrative_override=str → 上游 error。
    assert any("data_gap_acknowledged" in i["field"] for i in errors)
    assert any("narrative_override" in i["field"] for i in errors)
    ok, aniu_errors = UziLlmOrchestrator._validate_agent_analysis(bad)
    assert not ok  # Aniu 校验必须拒绝旧 schema


def test_missing_fields_still_rejected_as_shell() -> None:
    """空壳（只声明 agent_reviewed）仍被 Aniu 拦截（§5.3）。"""
    aa = _assemble()
    aa["_aniu_meta"] = {"panel_subtasks": {}, "qualitative_subtasks": {}}
    ok, errors = UziLlmOrchestrator._validate_agent_analysis(aa)
    assert not ok
    assert any("空壳" in e for e in errors)
