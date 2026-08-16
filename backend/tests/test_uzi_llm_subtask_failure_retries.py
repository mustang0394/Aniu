"""UZI LLM 子任务失败重试测试（文档 §13.5 / §20.3）。

单组子任务失败触发重试并最终 UZI_LLM_REVIEW_FAILED。
"""
from __future__ import annotations

from pathlib import Path
import json
import sys
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from tests.uzi_test_helpers import valid_uzi_synthesis, write_fake_stage1
from app.services.uzi_llm_orchestrator import (
    ERROR_LLM_REVIEW_FAILED,
    UziLlmOrchestrator,
    UziReviewError,
)


class _AlwaysFailingLLM:
    """每次调用都抛异常，模拟持续失败。"""

    def _call_llm_stream(self, *, base_url, api_key, payload,
                         timeout_seconds, cancel_event=None, max_retries=None):
        raise RuntimeError("大模型服务不可用")

    def run_structured_json_call(self, **kwargs):
        raise RuntimeError("大模型服务不可用")


def test_subtask_failure_retries_and_raises_llm_review_failed(tmp_path) -> None:
    write_fake_stage1(tmp_path / "uzi_reports", 1)
    app_settings = SimpleNamespace(
        llm_base_url="https://llm.example.com/v1",
        llm_api_key="sk-test",
        llm_model="m",
        llm_reasoning_effort=None,
        llm_max_retries=2,
        llm_timeout_seconds=10,
    )
    orch = UziLlmOrchestrator(llm_service=_AlwaysFailingLLM())
    orch._retry_delay_seconds = lambda attempt: 0
    with pytest.raises(UziReviewError) as exc_info:
        orch.run(
            report_id=1,
            app_settings=app_settings,
            report_root=tmp_path / "uzi_reports",
        )
    assert exc_info.value.error_code == ERROR_LLM_REVIEW_FAILED


def test_subtask_failure_does_not_write_agent_reviewed(tmp_path) -> None:
    """失败时绝不写入 agent_reviewed=true 的文件。"""
    write_fake_stage1(tmp_path / "uzi_reports", 2)
    app_settings = SimpleNamespace(
        llm_base_url="https://llm.example.com/v1",
        llm_api_key="sk-test",
        llm_model="m",
        llm_reasoning_effort=None,
        llm_max_retries=0,
        llm_timeout_seconds=10,
    )
    orch = UziLlmOrchestrator(llm_service=_AlwaysFailingLLM())
    orch._retry_delay_seconds = lambda attempt: 0
    with pytest.raises(UziReviewError):
        orch.run(
            report_id=2,
            app_settings=app_settings,
            report_root=tmp_path / "uzi_reports",
        )
    out = tmp_path / "uzi_reports" / "2" / "work" / "agent_analysis.json"
    if out.is_file():
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload.get("agent_reviewed") is not True, \
            "失败时不应写入 agent_reviewed=true"


def test_synthesis_subtask_failure_propagates(tmp_path) -> None:
    """综合组装阶段失败也触发 UZI_LLM_REVIEW_FAILED。"""
    write_fake_stage1(tmp_path / "uzi_reports", 3)

    class _PartialFailLLM:
        def _call_llm_stream(self, *, base_url, api_key, payload,
                             timeout_seconds, cancel_event=None, max_retries=None):
            sys_text = payload["messages"][0]["content"]
            if "综合组装" in sys_text:
                raise RuntimeError("综合阶段失败")
            if "一致性" in sys_text:
                content = json.dumps({"ok": True})
            else:
                content = json.dumps({"topic": "t", "conclusions": []})
            return {"choices": [{"message": {"content": content, "tool_calls": []}}]}

        def run_structured_json_call(self, **kwargs):
            raise RuntimeError("综合阶段失败")

    app_settings = SimpleNamespace(
        llm_base_url="https://llm.example.com/v1",
        llm_api_key="sk-test",
        llm_model="m",
        llm_reasoning_effort=None,
        llm_max_retries=0,
        llm_timeout_seconds=10,
    )
    orch = UziLlmOrchestrator(llm_service=_PartialFailLLM())
    orch._retry_delay_seconds = lambda attempt: 0
    with pytest.raises(UziReviewError) as exc_info:
        orch.run(
            report_id=3,
            app_settings=app_settings,
            report_root=tmp_path / "uzi_reports",
        )
    assert exc_info.value.error_code == ERROR_LLM_REVIEW_FAILED
