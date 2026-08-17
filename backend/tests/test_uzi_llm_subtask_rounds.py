"""UZI 子任务工具续轮与强制 JSON 收尾回归测试。"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.llm_service import LLMService, LLMUpstreamError
from app.services.uzi_llm_orchestrator import (
    ERROR_LLM_REVIEW_FAILED,
    UziLlmOrchestrator,
    UziReviewError,
)


def _settings(**overrides):
    defaults = {
        "llm_base_url": "https://llm.example.com/v1",
        "llm_api_key": "sk-test",
        "llm_model": "thinking-model",
        "llm_reasoning_effort": "high",
        "llm_enable_reasoning_content_echo": True,
        "llm_max_retries": 0,
        "llm_timeout_seconds": 30,
        "mx_api_key": "mx-test",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _subtask() -> dict:
    return {
        "id": "panel_c",
        "kind": "panel",
        "title": "风险与治理",
        "directives": "核对风险证据。",
        "categories": "risk",
        "investor_ids": ["risk-1"],
        "investor_roster": "risk-1（风险投资者）",
    }


def _tool_call(call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": "web_search",
            "arguments": json.dumps({"query": "公司治理风险"}),
        },
    }


def _tool_result(*_args, **_kwargs) -> dict:
    return {
        "ok": True,
        "tool_name": "web_search",
        "summary": "查询完成",
        "result": {"items": []},
    }


@pytest.mark.parametrize(
    ("echo_enabled", "reasoning_is_present"),
    [(True, True), (False, False)],
)
def test_tool_continuation_honors_reasoning_content_echo(
    caplog,
    tmp_path,
    echo_enabled: bool,
    reasoning_is_present: bool,
) -> None:
    calls: list[dict] = []

    class _FakeLLM:
        def _call_llm_stream(self, **kwargs):
            payload = copy.deepcopy(kwargs["payload"])
            calls.append(payload)
            if len(calls) == 1:
                return {
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": "",
                                "reasoning_content": "private-chain-of-thought",
                                "tool_calls": [_tool_call("call-1")],
                            },
                        }
                    ]
                }
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"topic": "风险"}),
                            "tool_calls": [],
                        },
                    }
                ]
            }

    orchestrator = UziLlmOrchestrator(llm_service=_FakeLLM())
    orchestrator.build_tools = lambda: [{"type": "function"}]
    orchestrator.execute_tool = _tool_result
    orchestrator._diagnostic_path = tmp_path / "llm-review.log"
    caplog.set_level(logging.INFO, logger="app.services.uzi_llm_orchestrator")

    result = orchestrator._run_subtask(
        subtask=_subtask(),
        stage1_text="{}",
        app_settings=_settings(
            llm_enable_reasoning_content_echo=echo_enabled,
        ),
        cancel_event=None,
    )

    assert result == {"topic": "风险"}
    assistant_messages = [
        message
        for message in calls[1]["messages"]
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    assert len(assistant_messages) == 1
    assert (
        "reasoning_content" in assistant_messages[0]
    ) is reasoning_is_present
    assert calls[1]["reasoning_effort"] == "high"
    assert "private-chain-of-thought" not in caplog.text
    diagnostic_text = orchestrator._diagnostic_path.read_text(encoding="utf-8")
    diagnostic_records = [
        json.loads(line) for line in diagnostic_text.splitlines()
    ]
    assert [record["phase"] for record in diagnostic_records] == ["tool", "tool"]
    assert diagnostic_records[0]["tools"] == ["web_search"]
    assert diagnostic_records[1]["json_valid"] is True
    assert "private-chain-of-thought" not in diagnostic_text


def test_tool_budget_exhaustion_gets_no_tool_json_round() -> None:
    calls: list[dict] = []

    class _FakeLLM:
        def _call_llm_stream(self, **kwargs):
            payload = copy.deepcopy(kwargs["payload"])
            calls.append(payload)
            if "tools" in payload:
                index = len(calls)
                return {
                    "choices": [
                        {
                            "finish_reason": "tool_calls",
                            "message": {
                                "content": "",
                                "tool_calls": [_tool_call(f"call-{index}")],
                            },
                        }
                    ]
                }
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"topic": "最终风险结论"}),
                            "tool_calls": [],
                        },
                    }
                ]
            }

    orchestrator = UziLlmOrchestrator(llm_service=_FakeLLM())
    orchestrator.build_tools = lambda: [{"type": "function"}]
    orchestrator.execute_tool = _tool_result

    result = orchestrator._run_subtask(
        subtask=_subtask(),
        stage1_text="{}",
        app_settings=_settings(),
        cancel_event=None,
    )

    assert result == {"topic": "最终风险结论"}
    assert len(calls) == 7
    assert all("tools" in payload for payload in calls[:6])
    assert "tools" not in calls[6]
    assert "tool_choice" not in calls[6]
    assert "工具查询阶段已经结束" in calls[6]["messages"][-1]["content"]


def test_truncated_output_gets_concise_forced_json_retry() -> None:
    calls: list[dict] = []

    class _FakeLLM:
        def _call_llm_stream(self, **kwargs):
            payload = copy.deepcopy(kwargs["payload"])
            calls.append(payload)
            if len(calls) == 1:
                return {
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {
                                "content": '{"topic":"被截断"',
                                "tool_calls": [],
                            },
                        }
                    ]
                }
            return {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps({"topic": "压缩后的完整结果"}),
                            "tool_calls": [],
                        },
                    }
                ]
            }

    orchestrator = UziLlmOrchestrator(llm_service=_FakeLLM())
    orchestrator.build_tools = lambda: [{"type": "function"}]

    result = orchestrator._run_subtask(
        subtask=_subtask(),
        stage1_text="{}",
        app_settings=_settings(),
        cancel_event=None,
    )

    assert result == {"topic": "压缩后的完整结果"}
    assert len(calls) == 2
    assert "tools" not in calls[1]
    assert "长度限制" in calls[1]["messages"][-1]["content"]


def test_uzi_does_not_retry_non_transient_400() -> None:
    attempts = 0

    class _FakeLLM:
        def _call_llm_stream(self, **_kwargs):
            nonlocal attempts
            attempts += 1
            raise LLMUpstreamError(
                "reasoning_content must be passed back",
                status_code=400,
            )

    orchestrator = UziLlmOrchestrator(llm_service=_FakeLLM())
    with pytest.raises(UziReviewError) as exc_info:
        orchestrator._call_llm(
            app_settings=_settings(llm_max_retries=3),
            payload={"model": "thinking-model", "messages": []},
            cancel_event=None,
        )

    assert exc_info.value.error_code == ERROR_LLM_REVIEW_FAILED
    assert "不可重试的 400" in exc_info.value.message
    assert attempts == 1


def test_reasoning_400_does_not_trigger_stream_options_fallback(monkeypatch) -> None:
    service = LLMService()
    payloads: list[dict] = []

    def _fail(*, payload, **_kwargs):
        payloads.append(payload)
        raise LLMUpstreamError(
            "reasoning_content must be passed back",
            status_code=400,
        )

    monkeypatch.setattr(service, "_consume_llm_stream", _fail)

    with pytest.raises(LLMUpstreamError):
        service._call_llm_stream(
            base_url="https://llm.example.com/v1",
            api_key="sk-test",
            payload={"model": "thinking-model", "messages": []},
            timeout_seconds=30,
        )

    assert len(payloads) == 1
    assert payloads[0]["stream_options"] == {"include_usage": True}
