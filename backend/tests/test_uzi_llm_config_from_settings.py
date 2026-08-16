"""UZI LLM 编排配置复用测试（文档 §13.1 / §20.3）。

- Base URL、模型、reasoning 和重试配置来自 AppSettings。
- API Key 只用于主服务 LLM 调用，不发送给 Worker。
"""

from __future__ import annotations

from pathlib import Path
import json
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.services.uzi_llm_orchestrator import (
    ERROR_LLM_NOT_CONFIGURED,
    UziLlmOrchestrator,
    UziReviewError,
)
from tests.uzi_test_helpers import (
    fake_llm_content_for_subtask,
    valid_uzi_synthesis,
    write_fake_stage1,
)


def test_orchestrator_uses_app_settings_config(monkeypatch, tmp_path) -> None:
    """_call_llm 透传 AppSettings 的 base_url/api_key/model/超时。"""
    from types import SimpleNamespace

    app_settings = SimpleNamespace(
        llm_base_url="https://llm.example.com/v1",
        llm_api_key="sk-test-123",
        llm_model="qwen-max",
        llm_reasoning_effort="medium",
        llm_max_retries=2,
        llm_timeout_seconds=99,
    )
    write_fake_stage1(tmp_path / "uzi_reports", 1)

    captured: dict = {}

    class _FakeLLM:
        def _call_llm_stream(self, *, base_url, api_key, payload,
                             timeout_seconds, cancel_event=None, max_retries=None):
            captured.update({
                "base_url": base_url,
                "api_key": api_key,
                "timeout": timeout_seconds,
                "max_retries": max_retries,
                "payload_model": payload["model"],
                "payload_reasoning": payload.get("reasoning_effort"),
            })
            system_text = payload["messages"][0]["content"]
            if "综合组装" in system_text:
                content = json.dumps(valid_uzi_synthesis())
            else:
                content = fake_llm_content_for_subtask(system_text)
            return {"choices": [{"message": {"content": content, "tool_calls": []}}]}

        def run_structured_json_call(self, **kwargs):
            return {"content": json.dumps(valid_uzi_synthesis()), "payload": {}}

    orchestrator = UziLlmOrchestrator(llm_service=_FakeLLM())
    orchestrator._retry_delay_seconds = lambda attempt: 0
    result = orchestrator.run(
        report_id=1,
        app_settings=app_settings,
        report_root=tmp_path / "uzi_reports",
    )
    assert captured["base_url"] == "https://llm.example.com/v1"
    assert captured["api_key"] == "sk-test-123"
    assert captured["timeout"] == 99
    assert captured["max_retries"] == 2
    assert captured["payload_model"] == "qwen-max"
    assert captured["payload_reasoning"] == "medium"
    written = (tmp_path / "uzi_reports" / "1" / "work" / "agent_analysis.json")
    assert written.is_file()
    assert result["agent_analysis"]["agent_reviewed"] is True


def test_missing_llm_config_raises_stable_code(monkeypatch, tmp_path) -> None:
    from types import SimpleNamespace

    app_settings = SimpleNamespace(
        llm_base_url=None,
        llm_api_key=None,
        llm_model="qwen-max",
    )
    write_fake_stage1(tmp_path / "uzi_reports", 1)
    orchestrator = UziLlmOrchestrator()
    try:
        orchestrator.run(
            report_id=1,
            app_settings=app_settings,
            report_root=tmp_path / "uzi_reports",
        )
        raise AssertionError("应当抛出 UZI_LLM_NOT_CONFIGURED")
    except UziReviewError as exc:
        assert exc.error_code == ERROR_LLM_NOT_CONFIGURED


def test_llm_deadline_caps_single_request_timeout(monkeypatch) -> None:
    from types import SimpleNamespace

    import app.services.uzi_llm_orchestrator as orchestrator_module

    captured: dict[str, int] = {}

    class _FakeLLM:
        def _call_llm_stream(self, **kwargs):
            captured["timeout"] = kwargs["timeout_seconds"]
            return {"choices": []}

    clock = [100.0]
    monkeypatch.setattr(orchestrator_module.time, "monotonic", lambda: clock[0])
    orchestrator = orchestrator_module.UziLlmOrchestrator(llm_service=_FakeLLM())
    orchestrator._deadline = 105.0
    settings = SimpleNamespace(
        llm_base_url="https://llm.example.com/v1",
        llm_api_key="sk-test",
        llm_model="m",
        llm_max_retries=0,
        llm_timeout_seconds=99,
    )

    orchestrator._call_llm(
        app_settings=settings,
        payload={"model": "m", "messages": []},
        cancel_event=None,
    )

    assert captured["timeout"] == 5


def test_llm_api_key_never_sent_to_worker(monkeypatch) -> None:
    """Worker 客户端请求体不含 LLM API Key（§13.1 / §20.3）。"""
    import httpx
    from app.services.uzi_worker_client import UziWorkerClient

    monkeypatch.setenv("UZI_WORKER_URL", "http://worker:9001")
    monkeypatch.setenv("UZI_WORKER_SHARED_SECRET", "worker-secret")
    monkeypatch.setenv("UZI_ENABLED", "1")
    get_settings.cache_clear()

    captured_bodies: list[dict] = []

    def _fake_request(self, method, url, **kwargs):
        captured_bodies.append(kwargs.get("json_body") or {})
        return httpx.Response(
            200, json={"job": {"status": "accepted"}}
        )

    client = UziWorkerClient()
    original = UziWorkerClient._request
    UziWorkerClient._request = _fake_request
    try:
        client.submit_stage1(
            report_id=7,
            ticker="600519.SH",
            report_rel_dir="7",
            mx_api_key="mx-secret",
        )
    finally:
        UziWorkerClient._request = original
        get_settings.cache_clear()

    assert captured_bodies, "应捕获到 Worker 请求体"
    body = captured_bodies[0]
    assert "llm_api_key" not in body
    assert "LLM_API_KEY" not in str(body).upper()
    assert body.get("mx_api_key") == "mx-secret"
    assert "Authorization" not in body or "Bearer sk-test" not in str(body)
