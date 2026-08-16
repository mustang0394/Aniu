from __future__ import annotations


def test_settings_telegram_fields_are_not_masked(monkeypatch, tmp_path) -> None:
    from app.core.config import get_settings
    from app.db import database as database_module
    from app.db.database import init_db, session_scope
    from app.schemas.aniu import AppSettingsRead, AppSettingsUpdate
    from app.services.aniu_service import aniu_service
    from app.services.trading_calendar_service import trading_calendar_service

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "tg_fields.db"))
    monkeypatch.setattr(trading_calendar_service, "ensure_months", lambda keys: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    init_db()

    bot_token = "7123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    chat_id = "-1001234567890"
    api_key = "sk-live-super-secret-key-123456"

    with session_scope() as db:
        settings = aniu_service.get_or_create_settings(db)
        updated = aniu_service.update_settings(
            db,
            AppSettingsUpdate(
                system_prompt=settings.system_prompt,
                tg_bot_token=bot_token,
                tg_chat_id=chat_id,
                llm_api_key=api_key,
                mx_api_key=api_key,
            ),
        )
        read = AppSettingsRead.model_validate(updated)

        # Telegram fields must remain fully readable/copyable in the settings UI.
        assert read.tg_bot_token == bot_token
        assert read.tg_chat_id == chat_id
        assert "****" not in (read.tg_bot_token or "")
        assert "****" not in (read.tg_chat_id or "")

        # API keys remain masked on read.
        assert read.llm_api_key is not None
        assert "****" in read.llm_api_key
        assert read.llm_api_key != api_key
        assert read.mx_api_key is not None
        assert "****" in read.mx_api_key
        assert read.mx_api_key != api_key

        # Masked API keys must not overwrite stored secrets on save.
        again = aniu_service.update_settings(
            db,
            AppSettingsUpdate(
                system_prompt=settings.system_prompt,
                tg_bot_token=bot_token,
                tg_chat_id=chat_id,
                llm_api_key=read.llm_api_key,
                mx_api_key=read.mx_api_key,
            ),
        )
        assert again.llm_api_key == api_key
        assert again.mx_api_key == api_key
        assert again.tg_bot_token == bot_token
        assert again.tg_chat_id == chat_id

    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()


def test_mask_key_still_used_for_api_keys() -> None:
    from app.schemas.aniu import _mask_key

    assert _mask_key(None) is None
    assert _mask_key("") == ""
    assert _mask_key("ab") == "****"
    assert _mask_key("abcdefgh") == "****gh"
    assert _mask_key("sk-live-secret-key") == "sk-****-key"
