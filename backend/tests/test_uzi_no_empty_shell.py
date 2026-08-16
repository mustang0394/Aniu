"""UZI 无效模型输出绝不能被标记为完整报告（文档 §5.3 / §13.5 / §22）。

- 子任务无法产出实质结果 → 组装后为空壳 → 校验拦截。
- 修复调用仍无法恢复实质内容 → UZI_AGENT_ANALYSIS_INVALID，
  不允许出现 agent_reviewed=true 的产物被 Stage 2 接受。
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


def test_empty_subtask_results_rejected_as_shell(tmp_path) -> None:
    """模型对所有子任务输出空 {}：组装后为空壳，必须失败。"""
    app_settings = _make_settings()
    write_fake_stage1(tmp_path / "uzi_reports", 1)

    class _FakeEmptyLLM:
        def __init__(self):
            self.repair_calls = 0

        def _call_llm_stream(self, *, base_url, api_key, payload,
                             timeout_seconds, cancel_event=None, max_retries=None):
            return {"choices": [{"message": {
                "content": "{}", "tool_calls": []}}]}

        def run_structured_json_call(self, **kwargs):
            self.repair_calls += 1
            # 修复调用仍输出空结构，无法恢复实质内容。
            return {"content": "{}", "payload": {}}

    fake_llm = _FakeEmptyLLM()
    orchestrator = UziLlmOrchestrator(llm_service=fake_llm)
    orchestrator._retry_delay_seconds = lambda attempt: 0
    try:
        orchestrator.run(
            report_id=1,
            app_settings=app_settings,
            report_root=tmp_path / "uzi_reports",
        )
        raise AssertionError("空壳结果必须失败")
    except UziReviewError as exc:
        assert exc.error_code == ERROR_AGENT_ANALYSIS_INVALID

    # 不允许出现 agent_reviewed=true 的产物文件。
    work = tmp_path / "uzi_reports" / "1" / "work"
    agent_file = work / "agent_analysis.json"
    if agent_file.is_file():
        payload = json.loads(agent_file.read_text(encoding="utf-8"))
        assert payload.get("agent_reviewed") is not True


def test_empty_subtask_results_after_repair_does_not_mark_complete(tmp_path) -> None:
    """修复一次仍为空壳 → UZI_AGENT_ANALYSIS_INVALID（§13.5 修复后仍失败）。"""
    app_settings = _make_settings()
    write_fake_stage1(tmp_path / "uzi_reports", 2)

    class _FakeLLM:
        def __init__(self):
            self.repair_calls = 0

        def _call_llm_stream(self, *, base_url, api_key, payload,
                             timeout_seconds, cancel_event=None, max_retries=None):
            return {"choices": [{"message": {
                "content": "{}", "tool_calls": []}}]}

        def run_structured_json_call(self, **kwargs):
            self.repair_calls += 1
            return {"content": "{}", "payload": {}}

    fake_llm = _FakeLLM()
    orchestrator = UziLlmOrchestrator(llm_service=fake_llm)
    orchestrator._retry_delay_seconds = lambda attempt: 0
    with pytest.raises(UziReviewError) as excinfo:
        orchestrator.run(
            report_id=2,
            app_settings=app_settings,
            report_root=tmp_path / "uzi_reports",
        )
    assert excinfo.value.error_code == ERROR_AGENT_ANALYSIS_INVALID
    assert fake_llm.repair_calls == 1