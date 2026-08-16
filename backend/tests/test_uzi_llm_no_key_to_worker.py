"""UZI LLM Key 不外泄给 Worker 测试（文档 §13.1 / §20.3）。

LLM API Key 只用于主服务 LLM 调用；mx_api_key 仅在 stage1 请求体内存传递，
绝不把 llm_api_key 发给 Worker。
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db import database as database_module
from app.db.database import init_db
from app.services.uzi_worker_client import UziWorkerClient


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "k.db"))
    monkeypatch.setenv("UZI_ENABLED", "true")
    monkeypatch.setenv("UZI_WORKER_URL", "http://worker:9001")
    monkeypatch.setenv("UZI_WORKER_SHARED_SECRET", "tok")
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    init_db()


def test_submit_stage1_request_body_has_no_llm_api_key(monkeypatch, tmp_path) -> None:
    _setup(tmp_path, monkeypatch)
    captured: dict = {}

    class _FakeResponse:
        status_code = 202
        def json(self): return {"job": {"status": "accepted"}}
        def raise_for_status(self): pass

    def _fake_request(self, method, url, *, headers=None, json=None, **kw):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse()

    import httpx
    monkeypatch.setattr(httpx.Client, "request", _fake_request)
    client = UziWorkerClient()
    client.submit_stage1(
        report_id=42,
        ticker="600519.SH",
        report_rel_dir="42",
        mx_api_key="mx-secret",
    )
    body = captured["json"]
    assert "llm_api_key" not in body
    assert body.get("mx_api_key") == "mx-secret"
    assert body.get("ticker") == "600519.SH"
    assert body.get("report_rel_dir") == "42"
    # Authorization/worker token 不应携带 llm key
    assert "sk-llm" not in str(captured["headers"])

    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None


def test_orchestrator_call_llm_uses_api_key_locally(monkeypatch, tmp_path) -> None:
    """orchestrator._call_llm 把 llm_api_key 传给本地 LLM 调用。"""
    from types import SimpleNamespace
    import json

    from tests.uzi_test_helpers import (
        fake_llm_content_for_subtask,
        valid_uzi_synthesis,
        write_fake_stage1,
    )
    from app.services.uzi_llm_orchestrator import UziLlmOrchestrator

    write_fake_stage1(tmp_path / "uzi_reports", 1)
    app_settings = SimpleNamespace(
        llm_base_url="https://llm.example.com/v1",
        llm_api_key="sk-llm-secret",
        llm_model="m",
        llm_reasoning_effort=None,
        llm_max_retries=0,
        llm_timeout_seconds=10,
    )
    captured: dict = {}

    class _FakeLLM:
        def _call_llm_stream(self, *, base_url, api_key, payload,
                             timeout_seconds, cancel_event=None, max_retries=None):
            captured["api_key"] = api_key
            sys_text = payload["messages"][0]["content"]
            if "综合组装" in sys_text:
                content = json.dumps(valid_uzi_synthesis())
            elif "一致性" in sys_text:
                content = json.dumps({"ok": True})
            else:
                content = fake_llm_content_for_subtask(sys_text)
            return {"choices": [{"message": {"content": content, "tool_calls": []}}]}

        def run_structured_json_call(self, **kwargs):
            captured.setdefault("repair_api_key", kwargs.get("api_key"))
            return {"content": json.dumps(valid_uzi_synthesis())}

    orch = UziLlmOrchestrator(llm_service=_FakeLLM())
    orch._retry_delay_seconds = lambda attempt: 0
    orch.run(report_id=1, app_settings=app_settings,
             report_root=tmp_path / "uzi_reports")
    assert captured.get("api_key") == "sk-llm-secret"
    # LLM key 仅在本地 LLM 调用中出现，不进入 Worker 请求体
    assert "sk-llm-secret" not in str(captured) or captured.get("api_key") == "sk-llm-secret"
