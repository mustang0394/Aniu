from __future__ import annotations

from types import SimpleNamespace


def test_normalize_and_apply_reasoning_effort() -> None:
    from app.services.llm_service import LLMService, normalize_reasoning_effort

    assert normalize_reasoning_effort(None) is None
    assert normalize_reasoning_effort("") is None
    assert normalize_reasoning_effort("   ") is None
    assert normalize_reasoning_effort("  high  ") == "high"

    service = LLMService()
    with_effort = service._apply_reasoning_effort({"model": "demo"}, "medium")
    assert with_effort["reasoning_effort"] == "medium"

    without_effort = service._apply_reasoning_effort(
        {"model": "demo", "reasoning_effort": "stale"}, "   "
    )
    assert "reasoning_effort" not in without_effort


def test_build_payload_includes_reasoning_effort_only_when_set() -> None:
    from app.services.llm_service import LLMService

    service = LLMService()
    base = SimpleNamespace(
        llm_model="demo-model",
        system_prompt="sys",
        task_prompt="do work",
        run_type="analysis",
        llm_reasoning_effort=None,
    )
    empty_payload = service.build_initial_request_payload(base)
    assert "reasoning_effort" not in empty_payload

    base.llm_reasoning_effort = " high "
    filled_payload = service.build_initial_request_payload(base)
    assert filled_payload["reasoning_effort"] == "high"

    from_messages = service.build_request_payload_from_messages(
        app_settings=base,
        messages=[{"role": "user", "content": "hello"}],
    )
    assert from_messages["reasoning_effort"] == "high"


def test_agent_loop_sends_reasoning_effort(monkeypatch) -> None:
    from app.services.llm_service import LLMService

    service = LLMService()
    seen_payloads: list[dict[str, object]] = []

    def fake_call_llm_stream(*, payload, **kwargs):
        del kwargs
        seen_payloads.append(dict(payload))
        return {
            "choices": [{"message": {"content": "done", "tool_calls": []}}],
            "stream_meta": {"final_streamed": False},
        }

    monkeypatch.setattr(service, "_call_llm_stream", fake_call_llm_stream)

    result = service._agent_loop(
        model="demo-model",
        base_url="https://example.com/v1",
        api_key="token",
        initial_messages=[{"role": "user", "content": "hi"}],
        run_type="analysis",
        timeout_seconds=5,
        tool_executor=lambda *_a, **_k: {},
        reasoning_effort="low",
    )
    assert result["final_answer"] == "done"
    assert seen_payloads
    assert seen_payloads[0]["reasoning_effort"] == "low"

    seen_payloads.clear()
    service._agent_loop(
        model="demo-model",
        base_url="https://example.com/v1",
        api_key="token",
        initial_messages=[{"role": "user", "content": "hi"}],
        run_type="analysis",
        timeout_seconds=5,
        tool_executor=lambda *_a, **_k: {},
        reasoning_effort="  ",
    )
    assert "reasoning_effort" not in seen_payloads[0]


def test_settings_reasoning_effort_roundtrip(monkeypatch, tmp_path) -> None:
    from app.core.config import get_settings
    from app.db import database as database_module
    from app.db.database import init_db, session_scope
    from app.schemas.aniu import AppSettingsRead, AppSettingsUpdate
    from app.services.aniu_service import aniu_service
    from app.services.trading_calendar_service import trading_calendar_service

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "reasoning_effort.db"))
    monkeypatch.setattr(trading_calendar_service, "ensure_months", lambda keys: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    init_db()

    with session_scope() as db:
        settings = aniu_service.get_or_create_settings(db)
        read_default = AppSettingsRead.model_validate(settings)
        assert read_default.llm_reasoning_effort is None

        updated = aniu_service.update_settings(
            db,
            AppSettingsUpdate(
                system_prompt=settings.system_prompt,
                llm_reasoning_effort="  medium  ",
            ),
        )
        read_updated = AppSettingsRead.model_validate(updated)
        assert read_updated.llm_reasoning_effort == "medium"
        assert updated.llm_reasoning_effort == "medium"

        cleared = aniu_service.update_settings(
            db,
            AppSettingsUpdate(
                system_prompt=settings.system_prompt,
                llm_reasoning_effort="   ",
            ),
        )
        read_cleared = AppSettingsRead.model_validate(cleared)
        assert read_cleared.llm_reasoning_effort is None
        assert cleared.llm_reasoning_effort is None

    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()
