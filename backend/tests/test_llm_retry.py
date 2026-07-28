from __future__ import annotations

import threading
from types import SimpleNamespace

import httpx
import pytest


def test_normalize_max_retries() -> None:
    from app.services.llm_service import normalize_max_retries

    assert normalize_max_retries(None) == 3
    assert normalize_max_retries("bad") == 3
    assert normalize_max_retries(-1) == 0
    assert normalize_max_retries(0) == 0
    assert normalize_max_retries(3) == 3
    assert normalize_max_retries(99) == 10
    assert normalize_max_retries("2") == 2


def test_is_retryable_llm_error() -> None:
    from app.services.llm_service import (
        LLMStreamCancelled,
        LLMUpstreamError,
        _is_retryable_llm_error,
    )

    assert _is_retryable_llm_error(LLMUpstreamError("限流", status_code=429))
    assert _is_retryable_llm_error(LLMUpstreamError("5xx", status_code=503))
    assert _is_retryable_llm_error(LLMUpstreamError("stream body error"))
    assert _is_retryable_llm_error(httpx.ReadTimeout("timeout"))
    assert _is_retryable_llm_error(httpx.ConnectError("boom"))
    assert _is_retryable_llm_error(
        RuntimeError("大模型接口请求超时 (30s)，请检查网络或增加超时时间。")
    )
    assert _is_retryable_llm_error(RuntimeError("大模型接口请求失败: connection reset"))

    assert not _is_retryable_llm_error(LLMUpstreamError("bad key", status_code=401))
    assert not _is_retryable_llm_error(LLMUpstreamError("bad req", status_code=400))
    assert not _is_retryable_llm_error(LLMUpstreamError("forbidden", status_code=403))
    assert not _is_retryable_llm_error(LLMStreamCancelled("gone"))
    assert not _is_retryable_llm_error(RuntimeError("大模型未返回 choices。"))


def test_retry_delay_seconds_capped(monkeypatch) -> None:
    from app.services import llm_service as llm_module

    monkeypatch.setattr(llm_module.random, "random", lambda: 0.5)
    assert llm_module._retry_delay_seconds(0) == pytest.approx(1.0)
    assert llm_module._retry_delay_seconds(1) == pytest.approx(2.0)
    assert llm_module._retry_delay_seconds(2) == pytest.approx(4.0)
    assert llm_module._retry_delay_seconds(3) == pytest.approx(8.0)
    assert llm_module._retry_delay_seconds(4) == pytest.approx(16.0)
    assert llm_module._retry_delay_seconds(5) == pytest.approx(20.0)
    assert llm_module._retry_delay_seconds(10) == pytest.approx(20.0)


def test_call_llm_stream_retries_then_succeeds(monkeypatch) -> None:
    from app.services.llm_service import LLMService, LLMUpstreamError

    service = LLMService()
    calls = {"n": 0}
    sleeps: list[float] = []
    events: list[tuple[str, dict]] = []

    def fake_once(**kwargs):
        del kwargs
        calls["n"] += 1
        if calls["n"] < 3:
            raise LLMUpstreamError("temporary", status_code=503)
        return {
            "choices": [{"message": {"content": "ok", "tool_calls": []}}],
            "stream_meta": {"final_streamed": True},
        }

    monkeypatch.setattr(service, "_call_llm_stream_once", fake_once)
    monkeypatch.setattr(
        "app.services.llm_service._sleep_with_cancel",
        lambda delay, cancel_event=None: sleeps.append(delay),
    )

    def emit(event_type: str, **data):
        events.append((event_type, data))

    result = service._call_llm_stream(
        base_url="https://example.com/v1",
        api_key="token",
        payload={"model": "demo", "messages": []},
        timeout_seconds=5,
        emit=emit,
        max_retries=3,
    )
    assert result["choices"][0]["message"]["content"] == "ok"
    assert calls["n"] == 3
    assert len(sleeps) == 2
    assert [event[0] for event in events] == ["llm_retry", "llm_retry"]
    assert events[0][1]["attempt"] == 1
    assert events[0][1]["max_retries"] == 3


def test_call_llm_stream_exhausts_retries(monkeypatch) -> None:
    from app.services.llm_service import LLMService, LLMUpstreamError

    service = LLMService()
    calls = {"n": 0}

    def fake_once(**kwargs):
        del kwargs
        calls["n"] += 1
        raise LLMUpstreamError("rate limited", status_code=429)

    monkeypatch.setattr(service, "_call_llm_stream_once", fake_once)
    monkeypatch.setattr(
        "app.services.llm_service._sleep_with_cancel",
        lambda delay, cancel_event=None: None,
    )

    with pytest.raises(LLMUpstreamError) as exc_info:
        service._call_llm_stream(
            base_url="https://example.com/v1",
            api_key="token",
            payload={"model": "demo", "messages": []},
            timeout_seconds=5,
            max_retries=2,
        )
    assert calls["n"] == 3
    assert "已重试 2 次仍失败" in str(exc_info.value)
    assert exc_info.value.status_code == 429


def test_call_llm_stream_no_retry_on_401(monkeypatch) -> None:
    from app.services.llm_service import LLMService, LLMUpstreamError

    service = LLMService()
    calls = {"n": 0}

    def fake_once(**kwargs):
        del kwargs
        calls["n"] += 1
        raise LLMUpstreamError("bad key", status_code=401)

    monkeypatch.setattr(service, "_call_llm_stream_once", fake_once)
    slept = {"n": 0}
    monkeypatch.setattr(
        "app.services.llm_service._sleep_with_cancel",
        lambda delay, cancel_event=None: slept.__setitem__("n", slept["n"] + 1),
    )

    with pytest.raises(LLMUpstreamError) as exc_info:
        service._call_llm_stream(
            base_url="https://example.com/v1",
            api_key="token",
            payload={"model": "demo", "messages": []},
            timeout_seconds=5,
            max_retries=3,
        )
    assert calls["n"] == 1
    assert slept["n"] == 0
    assert "已重试" not in str(exc_info.value)


def test_call_llm_stream_max_retries_zero(monkeypatch) -> None:
    from app.services.llm_service import LLMService, LLMUpstreamError

    service = LLMService()
    calls = {"n": 0}

    def fake_once(**kwargs):
        del kwargs
        calls["n"] += 1
        raise LLMUpstreamError("temporary", status_code=503)

    monkeypatch.setattr(service, "_call_llm_stream_once", fake_once)
    monkeypatch.setattr(
        "app.services.llm_service._sleep_with_cancel",
        lambda delay, cancel_event=None: (_ for _ in ()).throw(
            AssertionError("should not sleep")
        ),
    )

    with pytest.raises(LLMUpstreamError):
        service._call_llm_stream(
            base_url="https://example.com/v1",
            api_key="token",
            payload={"model": "demo", "messages": []},
            timeout_seconds=5,
            max_retries=0,
        )
    assert calls["n"] == 1


def test_call_llm_stream_cancel_during_backoff(monkeypatch) -> None:
    from app.services.llm_service import LLMService, LLMStreamCancelled, LLMUpstreamError

    service = LLMService()
    calls = {"n": 0}
    cancel_event = threading.Event()

    def fake_once(**kwargs):
        del kwargs
        calls["n"] += 1
        raise LLMUpstreamError("temporary", status_code=503)

    def fake_sleep(delay, cancel_event=None):
        del delay
        if cancel_event is not None:
            cancel_event.set()
        from app.services.llm_service import _raise_if_cancelled

        _raise_if_cancelled(cancel_event)

    monkeypatch.setattr(service, "_call_llm_stream_once", fake_once)
    monkeypatch.setattr("app.services.llm_service._sleep_with_cancel", fake_sleep)

    with pytest.raises(LLMStreamCancelled):
        service._call_llm_stream(
            base_url="https://example.com/v1",
            api_key="token",
            payload={"model": "demo", "messages": []},
            timeout_seconds=5,
            cancel_event=cancel_event,
            max_retries=3,
        )
    assert calls["n"] == 1


def test_include_usage_fallback_does_not_consume_retry_quota(monkeypatch) -> None:
    from app.services.llm_service import LLMService, LLMUpstreamError

    service = LLMService()
    consume_calls: list[bool] = []

    def fake_consume(*, payload, **kwargs):
        del kwargs
        include_usage = bool((payload or {}).get("stream_options"))
        consume_calls.append(include_usage)
        if include_usage:
            raise LLMUpstreamError("unsupported stream_options", status_code=400)
        return {
            "choices": [{"message": {"content": "ok", "tool_calls": []}}],
            "stream_meta": {"final_streamed": True},
        }

    monkeypatch.setattr(service, "_consume_llm_stream", fake_consume)
    monkeypatch.setattr(
        "app.services.llm_service._sleep_with_cancel",
        lambda delay, cancel_event=None: (_ for _ in ()).throw(
            AssertionError("transient retry should not run")
        ),
    )

    result = service._call_llm_stream(
        base_url="https://example.com/v1",
        api_key="token",
        payload={"model": "demo", "messages": []},
        timeout_seconds=5,
        max_retries=3,
    )
    assert result["choices"][0]["message"]["content"] == "ok"
    assert consume_calls == [True, False]


def test_agent_loop_passes_max_retries(monkeypatch) -> None:
    from app.services.llm_service import LLMService

    service = LLMService()
    seen: dict[str, object] = {}

    def fake_call_llm_stream(**kwargs):
        seen["max_retries"] = kwargs.get("max_retries")
        return {
            "choices": [{"message": {"content": "done", "tool_calls": []}}],
            "stream_meta": {"final_streamed": False},
        }

    monkeypatch.setattr(service, "_call_llm_stream", fake_call_llm_stream)
    service._agent_loop(
        model="demo-model",
        base_url="https://example.com/v1",
        api_key="token",
        initial_messages=[{"role": "user", "content": "hi"}],
        run_type="analysis",
        timeout_seconds=5,
        tool_executor=lambda *_a, **_k: {},
        max_retries=5,
    )
    assert seen["max_retries"] == 5


def test_chat_reads_max_retries_from_app_settings(monkeypatch) -> None:
    from app.services.llm_service import LLMService

    service = LLMService()
    seen: dict[str, object] = {}

    def fake_agent_loop(**kwargs):
        seen["max_retries"] = kwargs.get("max_retries")
        return {
            "final_answer": "hello",
            "tool_history": [],
            "responses": [],
            "final_message": {},
            "messages": [],
        }

    monkeypatch.setattr(service, "_agent_loop", fake_agent_loop)
    answer = service.chat(
        model="demo",
        base_url="https://example.com/v1",
        api_key="token",
        system_prompt="sys",
        messages=[{"role": "user", "content": "hi"}],
        tool_context={
            "app_settings": SimpleNamespace(llm_max_retries=7, llm_reasoning_effort=None)
        },
    )
    assert answer == "hello"
    assert seen["max_retries"] == 7


def test_settings_llm_max_retries_roundtrip(monkeypatch, tmp_path) -> None:
    from app.core.config import get_settings
    from app.db import database as database_module
    from app.db.database import init_db, session_scope
    from app.schemas.aniu import AppSettingsRead, AppSettingsUpdate
    from app.services.aniu_service import aniu_service
    from app.services.trading_calendar_service import trading_calendar_service

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "llm_max_retries.db"))
    monkeypatch.setattr(trading_calendar_service, "ensure_years", lambda years: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    init_db()

    with session_scope() as db:
        settings = aniu_service.get_or_create_settings(db)
        read_default = AppSettingsRead.model_validate(settings)
        assert read_default.llm_max_retries == 3

        updated = aniu_service.update_settings(
            db,
            AppSettingsUpdate(
                system_prompt=settings.system_prompt,
                llm_max_retries=5,
            ),
        )
        read_updated = AppSettingsRead.model_validate(updated)
        assert read_updated.llm_max_retries == 5
        assert updated.llm_max_retries == 5

        clamped = aniu_service.update_settings(
            db,
            AppSettingsUpdate(
                system_prompt=settings.system_prompt,
                llm_max_retries=99,
            ),
        )
        assert clamped.llm_max_retries == 10

    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()


def test_settings_llm_max_retries_schema_bounds() -> None:
    from app.schemas.aniu import AppSettingsUpdate

    assert AppSettingsUpdate(system_prompt="x", llm_max_retries=0).llm_max_retries == 0
    assert AppSettingsUpdate(system_prompt="x", llm_max_retries=10).llm_max_retries == 10
    assert AppSettingsUpdate(system_prompt="x", llm_max_retries=-3).llm_max_retries == 0
    assert AppSettingsUpdate(system_prompt="x", llm_max_retries=50).llm_max_retries == 10
