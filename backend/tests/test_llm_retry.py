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


# ---------------------------------------------------------------------------
# _call_llm_stream is now a single logical attempt (whole-round retry lives in
# _run_agent_loop_with_retry). The include_usage 400 fallback remains inside
# _call_llm_stream_once and must not sleep.
# ---------------------------------------------------------------------------


def test_call_llm_stream_single_attempt_no_retry(monkeypatch) -> None:
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
            AssertionError("transient retry should not run")
        ),
    )

    with pytest.raises(LLMUpstreamError):
        service._call_llm_stream(
            base_url="https://example.com/v1",
            api_key="token",
            payload={"model": "demo", "messages": []},
            timeout_seconds=5,
            max_retries=3,
        )
    assert calls["n"] == 1


def test_call_llm_stream_no_retry_on_401(monkeypatch) -> None:
    from app.services.llm_service import LLMService, LLMUpstreamError

    service = LLMService()
    calls = {"n": 0}

    def fake_once(**kwargs):
        del kwargs
        calls["n"] += 1
        raise LLMUpstreamError("bad key", status_code=401)

    monkeypatch.setattr(service, "_call_llm_stream_once", fake_once)
    monkeypatch.setattr(
        "app.services.llm_service._sleep_with_cancel",
        lambda delay, cancel_event=None: (_ for _ in ()).throw(
            AssertionError("should not sleep")
        ),
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
    assert "已重试" not in str(exc_info.value)


def test_include_usage_fallback_does_not_sleep(monkeypatch) -> None:
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


# ---------------------------------------------------------------------------
# _run_agent_loop_with_retry — the unified whole-round retry policy.
# ---------------------------------------------------------------------------


def _make_result(*, final_answer: str, tool_history: list | None = None) -> dict:
    return {
        "final_answer": final_answer,
        "raw_final_answer": final_answer,
        "tool_history": tool_history or [],
        "responses": [],
        "final_message": {},
        "messages": [],
    }


def _patch_no_sleep(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.llm_service._sleep_with_cancel",
        lambda delay, cancel_event=None: None,
    )


def test_agent_loop_with_retry_analysis_no_tools_retries_then_fails(monkeypatch) -> None:
    from app.services.llm_service import LLMService

    service = LLMService()
    calls = {"n": 0}

    def fake_agent_loop(**kwargs):
        del kwargs
        calls["n"] += 1
        return _make_result(final_answer="分析结论", tool_history=[])

    monkeypatch.setattr(service, "_agent_loop", fake_agent_loop)
    _patch_no_sleep(monkeypatch)

    with pytest.raises(Exception):
        service._run_agent_loop_with_retry(
            model="demo",
            base_url="https://example.com/v1",
            api_key="token",
            initial_messages=[{"role": "user", "content": "hi"}],
            run_type="analysis",
            timeout_seconds=5,
            tool_executor=lambda *_a, **_k: {},
            max_retries=2,
        )
    # 1 initial + 2 retries = 3 whole-round attempts.
    assert calls["n"] == 3


def test_agent_loop_with_retry_analysis_with_tools_succeeds(monkeypatch) -> None:
    from app.services.llm_service import LLMService

    service = LLMService()
    calls = {"n": 0}

    def fake_agent_loop(**kwargs):
        del kwargs
        calls["n"] += 1
        return _make_result(
            final_answer="分析结论",
            tool_history=[{"name": "mx_quote", "arguments": {}, "result": {"ok": True}}],
        )

    monkeypatch.setattr(service, "_agent_loop", fake_agent_loop)
    _patch_no_sleep(monkeypatch)

    result = service._run_agent_loop_with_retry(
        model="demo",
        base_url="https://example.com/v1",
        api_key="token",
        initial_messages=[{"role": "user", "content": "hi"}],
        run_type="analysis",
        timeout_seconds=5,
        tool_executor=lambda *_a, **_k: {},
        max_retries=3,
    )
    assert calls["n"] == 1
    assert result["tool_history"]


def test_agent_loop_with_retry_trade_no_tools_retries(monkeypatch) -> None:
    from app.services.llm_service import LLMService

    service = LLMService()
    calls = {"n": 0}

    def fake_agent_loop(**kwargs):
        del kwargs
        calls["n"] += 1
        return _make_result(final_answer="建议买入", tool_history=[])

    monkeypatch.setattr(service, "_agent_loop", fake_agent_loop)
    _patch_no_sleep(monkeypatch)

    with pytest.raises(Exception):
        service._run_agent_loop_with_retry(
            model="demo",
            base_url="https://example.com/v1",
            api_key="token",
            initial_messages=[{"role": "user", "content": "hi"}],
            run_type="trade",
            timeout_seconds=5,
            tool_executor=lambda *_a, **_k: {},
            max_retries=1,
        )
    assert calls["n"] == 2


def test_agent_loop_with_retry_chat_empty_retries(monkeypatch) -> None:
    from app.services.llm_service import LLMService

    service = LLMService()
    calls = {"n": 0}

    def fake_agent_loop(**kwargs):
        del kwargs
        calls["n"] += 1
        return _make_result(final_answer="", tool_history=[])

    monkeypatch.setattr(service, "_agent_loop", fake_agent_loop)
    _patch_no_sleep(monkeypatch)

    with pytest.raises(Exception):
        service._run_agent_loop_with_retry(
            model="demo",
            base_url="https://example.com/v1",
            api_key="token",
            initial_messages=[{"role": "user", "content": "hi"}],
            run_type="chat",
            timeout_seconds=5,
            tool_executor=lambda *_a, **_k: {},
            max_retries=2,
        )
    assert calls["n"] == 3


def test_agent_loop_with_retry_chat_text_without_tools_succeeds(monkeypatch) -> None:
    from app.services.llm_service import LLMService

    service = LLMService()
    calls = {"n": 0}

    def fake_agent_loop(**kwargs):
        del kwargs
        calls["n"] += 1
        return _make_result(final_answer="纯文字回答", tool_history=[])

    monkeypatch.setattr(service, "_agent_loop", fake_agent_loop)
    _patch_no_sleep(monkeypatch)

    result = service._run_agent_loop_with_retry(
        model="demo",
        base_url="https://example.com/v1",
        api_key="token",
        initial_messages=[{"role": "user", "content": "hi"}],
        run_type="chat",
        timeout_seconds=5,
        tool_executor=lambda *_a, **_k: {},
        max_retries=3,
    )
    assert calls["n"] == 1
    assert result["final_answer"] == "纯文字回答"


def test_agent_loop_with_retry_exception_retries_then_raises(monkeypatch) -> None:
    from app.services.llm_service import LLMService, LLMUpstreamError

    service = LLMService()
    calls = {"n": 0}

    def fake_agent_loop(**kwargs):
        del kwargs
        calls["n"] += 1
        raise LLMUpstreamError("5xx", status_code=503)

    monkeypatch.setattr(service, "_agent_loop", fake_agent_loop)
    _patch_no_sleep(monkeypatch)

    with pytest.raises(LLMUpstreamError) as exc_info:
        service._run_agent_loop_with_retry(
            model="demo",
            base_url="https://example.com/v1",
            api_key="token",
            initial_messages=[{"role": "user", "content": "hi"}],
            run_type="analysis",
            timeout_seconds=5,
            tool_executor=lambda *_a, **_k: {},
            max_retries=2,
        )
    assert calls["n"] == 3
    assert "已重试 2 次仍失败" in str(exc_info.value)


def test_agent_loop_with_retry_non_retryable_status_still_retries(monkeypatch) -> None:
    """Unified policy: every failure (even 401/400) triggers a whole-round retry."""
    from app.services.llm_service import LLMService, LLMUpstreamError

    service = LLMService()
    calls = {"n": 0}

    def fake_agent_loop(**kwargs):
        del kwargs
        calls["n"] += 1
        raise LLMUpstreamError("bad key", status_code=401)

    monkeypatch.setattr(service, "_agent_loop", fake_agent_loop)
    _patch_no_sleep(monkeypatch)

    with pytest.raises(LLMUpstreamError):
        service._run_agent_loop_with_retry(
            model="demo",
            base_url="https://example.com/v1",
            api_key="token",
            initial_messages=[{"role": "user", "content": "hi"}],
            run_type="analysis",
            timeout_seconds=5,
            tool_executor=lambda *_a, **_k: {},
            max_retries=1,
        )
    # 401 now also retried under the unified whole-round policy.
    assert calls["n"] == 2


def test_agent_loop_with_retry_cancel_not_retried(monkeypatch) -> None:
    from app.services.llm_service import LLMService, LLMStreamCancelled

    service = LLMService()
    calls = {"n": 0}
    cancel_event = threading.Event()

    def fake_agent_loop(**kwargs):
        del kwargs
        calls["n"] += 1
        raise LLMStreamCancelled("客户端连接已断开。")

    monkeypatch.setattr(service, "_agent_loop", fake_agent_loop)
    _patch_no_sleep(monkeypatch)

    with pytest.raises(LLMStreamCancelled):
        service._run_agent_loop_with_retry(
            model="demo",
            base_url="https://example.com/v1",
            api_key="token",
            initial_messages=[{"role": "user", "content": "hi"}],
            run_type="chat",
            timeout_seconds=5,
            tool_executor=lambda *_a, **_k: {},
            cancel_event=cancel_event,
            max_retries=3,
        )
    assert calls["n"] == 1


def test_agent_loop_with_retry_retries_then_succeeds(monkeypatch) -> None:
    """First round invalid (analysis, no tools) then second round valid."""
    from app.services.llm_service import LLMService

    service = LLMService()
    calls = {"n": 0}
    events: list[tuple[str, dict]] = []

    def fake_agent_loop(**kwargs):
        del kwargs
        calls["n"] += 1
        if calls["n"] < 2:
            return _make_result(final_answer="分析结论", tool_history=[])
        return _make_result(
            final_answer="分析结论",
            tool_history=[{"name": "mx_quote", "arguments": {}, "result": {"ok": True}}],
        )

    monkeypatch.setattr(service, "_agent_loop", fake_agent_loop)
    _patch_no_sleep(monkeypatch)

    def emit(event_type: str, **data):
        events.append((event_type, data))

    result = service._run_agent_loop_with_retry(
        model="demo",
        base_url="https://example.com/v1",
        api_key="token",
        initial_messages=[{"role": "user", "content": "hi"}],
        run_type="analysis",
        timeout_seconds=5,
        tool_executor=lambda *_a, **_k: {},
        emit=emit,
        max_retries=3,
    )
    assert calls["n"] == 2
    assert result["tool_history"]
    assert [event[0] for event in events] == ["llm_retry"]
    assert events[0][1]["attempt"] == 1
    assert events[0][1]["max_retries"] == 3


def test_agent_loop_with_retry_max_retries_zero(monkeypatch) -> None:
    from app.services.llm_service import LLMService

    service = LLMService()
    calls = {"n": 0}

    def fake_agent_loop(**kwargs):
        del kwargs
        calls["n"] += 1
        return _make_result(final_answer="分析结论", tool_history=[])

    monkeypatch.setattr(service, "_agent_loop", fake_agent_loop)
    _patch_no_sleep(monkeypatch)

    with pytest.raises(Exception):
        service._run_agent_loop_with_retry(
            model="demo",
            base_url="https://example.com/v1",
            api_key="token",
            initial_messages=[{"role": "user", "content": "hi"}],
            run_type="analysis",
            timeout_seconds=5,
            tool_executor=lambda *_a, **_k: {},
            max_retries=0,
        )
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Plumbing: _agent_loop still forwards max_retries to _call_llm_stream (now a
# no-op for retries but kept for signature compatibility), and chat() forwards
# llm_max_retries from app settings into the wrapper.
# ---------------------------------------------------------------------------


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

    def fake_wrapper(**kwargs):
        seen["max_retries"] = kwargs.get("max_retries")
        return {
            "final_answer": "hello",
            "raw_final_answer": "hello",
            "tool_history": [],
            "responses": [],
            "final_message": {},
            "messages": [],
        }

    monkeypatch.setattr(service, "_run_agent_loop_with_retry", fake_wrapper)
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
