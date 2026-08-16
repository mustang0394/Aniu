from __future__ import annotations

import pytest
from pydantic import ValidationError


def test_settings_app_display_name_roundtrip(monkeypatch, tmp_path) -> None:
    from app.core.config import get_settings
    from app.core.constants import DEFAULT_APP_DISPLAY_NAME
    from app.db import database as database_module
    from app.db.database import init_db, session_scope
    from app.schemas.aniu import AppSettingsRead, AppSettingsUpdate
    from app.services.aniu_service import aniu_service
    from app.services.trading_calendar_service import trading_calendar_service

    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "display_name.db"))
    monkeypatch.setattr(trading_calendar_service, "ensure_months", lambda keys: None)
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None
    init_db()

    with session_scope() as db:
        settings = aniu_service.get_or_create_settings(db)
        read_default = AppSettingsRead.model_validate(settings)
        assert read_default.app_display_name == DEFAULT_APP_DISPLAY_NAME

        updated = aniu_service.update_settings(
            db,
            AppSettingsUpdate(
                system_prompt=settings.system_prompt,
                app_display_name="测试站",
            ),
        )
        read_updated = AppSettingsRead.model_validate(updated)
        assert read_updated.app_display_name == "测试站"

        blanked = aniu_service.update_settings(
            db,
            AppSettingsUpdate(
                system_prompt=settings.system_prompt,
                app_display_name="   ",
            ),
        )
        read_blank = AppSettingsRead.model_validate(blanked)
        assert read_blank.app_display_name == DEFAULT_APP_DISPLAY_NAME

        emptied = aniu_service.update_settings(
            db,
            AppSettingsUpdate(
                system_prompt=settings.system_prompt,
                app_display_name="",
            ),
        )
        read_empty = AppSettingsRead.model_validate(emptied)
        assert read_empty.app_display_name == DEFAULT_APP_DISPLAY_NAME

    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()


def test_app_display_name_rejects_overlong() -> None:
    from app.schemas.aniu import AppSettingsUpdate

    with pytest.raises(ValidationError):
        AppSettingsUpdate(
            system_prompt="x",
            app_display_name="A" * 65,
        )


def test_app_display_name_strips_whitespace() -> None:
    from app.schemas.aniu import AppSettingsUpdate

    payload = AppSettingsUpdate(
        system_prompt="x",
        app_display_name="  牛牛  ",
    )
    assert payload.app_display_name == "牛牛"
