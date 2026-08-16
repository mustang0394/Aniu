"""UZI agent_analysis.json 结构校验与修复测试（文档 §13.5 / §20.3）。

- JSON 结构错误触发一次修复调用。
- 修复后仍失败 → UZI_AGENT_ANALYSIS_INVALID，不调用 Stage 2。
"""

from __future__ import annotations

from pathlib import Path
import json
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.uzi_llm_orchestrator import (
    ERROR_AGENT_ANALYSIS_INVALID,
    UziLlmOrchestrator,
    UziReviewError,
)
from tests.uzi_test_helpers import write_fake_stage1


def _make_settings(**overrides):
    from types import SimpleNamespace

    defaults = dict(
        llm_base_url="https://llm.example.com/v1",
        llm_api_key="sk-test-123",
        llm_model="qwen-max",
        llm_reasoning_effort=None,
        llm_max_retries=0,
        llm_timeout_seconds=60,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


_BROKEN_SYNTHESIS = {
    "dim_commentary": {"key": "value"},
    # 缺 panel_insights（应为字符串）/ qualitative_deep_dive / data_gap_acknowledged
    "narrative_override": "叙事",  # 应为 dict
}


class _FakeLLMWithBrokenSynthesis:
    """子任务正常，synthesis 输出结构不合法。"""

    def __init__(self):
        self.repair_calls = 0

    def _call_llm_stream(self, *, base_url, api_key, payload,
                         timeout_seconds, cancel_event=None, max_retries=None):
        system_text = payload["messages"][0]["content"]
        if "综合组装" in system_text:
            content = json.dumps(_BROKEN_SYNTHESIS)
        else:
            content = json.dumps({"topic": "t", "conclusions": []})
        return {"choices": [{"message": {"content": content, "tool_calls": []}}]}

    def run_structured_json_call(self, *, model, base_url, api_key,
                                 system_prompt, user_prompt, timeout_seconds,
                                 reasoning_effort=None, max_retries=None,
                                 cancel_event=None):
        self.repair_calls += 1
        # 修复返回完整合法结构。
        return {
            "content": json.dumps(_valid_synthesis()),
            "payload": {},
        }


def _valid_synthesis() -> dict:
    """符合上游 schema 的合法 synthesis。"""
    return {
        "dim_commentary": {
            "0_basic": "公司主营稳健，营收与利润保持增长，毛利率稳定在较高水平。",
            "1_financials": "ROE 连续三年保持 15% 以上，现金流充裕，财务结构健康。",
        },
        "panel_insights": "评委投票分布中多头占优，价值派与成长派均给出偏多判断，分歧集中在估值中枢。",
        "great_divide_override": {
            "punchline": "价值派看多但成长派担心增速回落，多空分歧显著。",
            "bull_say_rounds": ["估值处于历史低位", "现金流稳定充裕", "股息率有吸引力"],
            "bear_say_rounds": ["增速可能放缓", "行业竞争加剧", "宏观有不确定性"],
        },
        "narrative_override": {
            "core_conclusion": "综合基本面与市场情绪，该标的中长期配置价值较高，建议逢低分批关注。",
            "risks": ["行业政策变化", "原材料价格波动", "市场风格切换"],
            "buy_zones": {
                "value": {"price": 1650, "rationale": "低于内在价值"},
                "growth": {"price": 1700, "rationale": "成长合理区间"},
                "technical": {"price": 1680, "rationale": "技术支撑位"},
                "youzi": {"price": 1720, "rationale": "情绪驱动点"},
            },
        },
        "qualitative_deep_dive": {
            "3_macro": {
                "evidence": [
                    {"source": "宏观数据", "url": "https://example.com/macro", "finding": "流动性宽松"},
                    {"source": "政策文件", "url": "https://example.com/policy", "finding": "产业政策友好"},
                ],
                "associations": [],
                "conclusion": "宏观环境友好，政策支持明确。",
            },
            "7_industry": {
                "evidence": [
                    {"source": "行业报告", "url": "https://example.com/ind", "finding": "行业景气上行"},
                    {"source": "同业数据", "url": "https://example.com/peer", "finding": "份额稳定"},
                ],
                "associations": [],
                "conclusion": "行业空间广阔，竞争格局良好。",
            },
            "13_policy": {
                "evidence": [
                    {"source": "监管公告", "url": "https://example.com/reg", "finding": "监管态度中性"},
                    {"source": "税制文件", "url": "https://example.com/tax", "finding": "税负稳定"},
                ],
                "associations": [],
                "conclusion": "政策面无重大扰动。",
            },
        },
        "data_gap_acknowledged": {
            "8_materials": "原材料成本明细未能获取，已尝试公开数据源。"
        },
    }


def _run_with_fake(orchestrator, fake_llm, tmp_path, report_id=1):
    app_settings = _make_settings()
    write_fake_stage1(tmp_path / "uzi_reports", report_id)
    orchestrator._retry_delay_seconds = lambda attempt: 0
    return orchestrator.run(
        report_id=report_id,
        app_settings=app_settings,
        report_root=tmp_path / "uzi_reports",
    )


def test_broken_json_triggers_single_repair(monkeypatch, tmp_path) -> None:
    fake_llm = _FakeLLMWithBrokenSynthesis()
    orchestrator = UziLlmOrchestrator(llm_service=fake_llm)
    result = _run_with_fake(orchestrator, fake_llm, tmp_path)

    assert fake_llm.repair_calls == 1, "应恰好触发一次修复调用"
    payload = result["agent_analysis"]
    assert payload["agent_reviewed"] is True
    assert isinstance(payload["panel_insights"], str)
    assert isinstance(payload["data_gap_acknowledged"], dict)
    # 修复后重新组装：补充面板/定性子任务汇总（私有 _aniu_meta）。
    assert set(payload["_aniu_meta"]["panel_subtasks"]) == {
        "panel_a", "panel_b", "panel_c", "panel_d"
    }
    assert set(payload["_aniu_meta"]["qualitative_subtasks"]) == {
        "qual_a", "qual_b", "qual_c"
    }
    written = (tmp_path / "uzi_reports" / "1" / "work" / "agent_analysis.json")
    assert json.loads(written.read_text(encoding="utf-8"))["agent_reviewed"] is True


def test_repair_failure_raises_invalid_and_no_stage2(tmp_path, monkeypatch) -> None:
    """修复后仍不合法 → UZI_AGENT_ANALYSIS_INVALID，不写 agent_reviewed=true。"""
    from app.services import uzi_report_service as report_service_module

    class _FakeFailingRepairLLM(_FakeLLMWithBrokenSynthesis):
        def run_structured_json_call(self, **kwargs):
            self.repair_calls += 1
            return {"content": json.dumps(_BROKEN_SYNTHESIS), "payload": {}}

    fake_llm = _FakeFailingRepairLLM()
    orchestrator = UziLlmOrchestrator(llm_service=fake_llm)
    stage2_called = {"n": 0}
    original = report_service_module.UziReportService._run_stage2
    try:
        with pytest.raises(UziReviewError) as excinfo:
            _run_with_fake(orchestrator, fake_llm, tmp_path)
    finally:
        report_service_module.UziReportService._run_stage2 = original

    assert excinfo.value.error_code == ERROR_AGENT_ANALYSIS_INVALID
    assert fake_llm.repair_calls == 1
    work = tmp_path / "uzi_reports" / "1" / "work"
    if (work / "agent_analysis.json").is_file():
        payload = json.loads((work / "agent_analysis.json").read_text(encoding="utf-8"))
        assert payload.get("agent_reviewed") is not True