from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db import database as database_module
from app.db.database import init_db, session_scope
from app.db.models import StrategyRun, StrategySchedule, TradeOrder
from app.services.aniu_service import aniu_service


def _use_temp_db(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("SQLITE_DB_PATH", str(tmp_path / "test.db"))
    get_settings.cache_clear()
    database_module._engine = None
    database_module._session_local = None


def _reset_db_state() -> None:
    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()


def test_compute_next_run_at_returns_utc() -> None:
    result = aniu_service._compute_next_run_at("15 7 * * 1-5")

    assert result is not None
    assert result.tzinfo == timezone.utc


def test_compute_next_run_at_recomputes_future_time_in_utc() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 4, 12, 8, 46, tzinfo=shanghai)

    result = aniu_service._compute_next_run_at("45 8 * * 1-5", from_time=start)

    assert result is not None
    assert result.tzinfo == timezone.utc
    in_shanghai = result.astimezone(shanghai)
    assert in_shanghai.hour == 8
    assert in_shanghai.minute == 45
    assert in_shanghai.date().isoformat() == "2026-04-13"


def test_compute_next_run_at_preserves_utc_timezone() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 4, 12, 7, 44, tzinfo=shanghai)

    result = aniu_service._compute_next_run_at("45 7 * * 1-5", from_time=start)

    assert result is not None
    assert result.tzinfo == timezone.utc


def test_compute_next_run_at_respects_day_of_week_field() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 4, 13, 10, 0, tzinfo=shanghai)

    result = aniu_service._compute_next_run_at("0 11 * * 2", from_time=start)

    assert result is not None
    in_shanghai = result.astimezone(shanghai)
    assert in_shanghai.date().isoformat() == "2026-04-14"
    assert in_shanghai.hour == 11
    assert in_shanghai.minute == 0


def test_compute_next_run_at_respects_day_and_month_fields() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 4, 13, 8, 0, tzinfo=shanghai)

    result = aniu_service._compute_next_run_at("15 9 15 4 *", from_time=start)

    assert result is not None
    in_shanghai = result.astimezone(shanghai)
    assert in_shanghai.date().isoformat() == "2026-04-15"
    assert in_shanghai.hour == 9
    assert in_shanghai.minute == 15


def test_compute_next_run_at_supports_range_step_expression() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 4, 13, 9, 10, tzinfo=shanghai)

    result = aniu_service._compute_next_run_at("10-14/2 9 * * 1-5", from_time=start)

    assert result is not None
    in_shanghai = result.astimezone(shanghai)
    assert in_shanghai.date().isoformat() == "2026-04-13"
    assert in_shanghai.hour == 9
    assert in_shanghai.minute == 12


def test_schedule_datetimes_are_stored_as_utc() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    result = aniu_service._compute_next_run_at(
        "15 7 * * 1-5",
        from_time=datetime(2026, 4, 12, 7, 0, tzinfo=shanghai),
    )

    assert result is not None
    assert result.isoformat().endswith("+00:00")
    in_shanghai = result.astimezone(shanghai)
    assert in_shanghai.hour == 7
    assert in_shanghai.minute == 15


def test_compute_next_run_at_skips_weekend_to_next_trading_day() -> None:
    shanghai = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 4, 11, 8, 0, tzinfo=shanghai)

    result = aniu_service._compute_next_run_at("0 8 * * 1-5", from_time=start)

    assert result is not None
    assert result.date().isoformat() == "2026-04-13"


def test_compute_next_run_at_skips_non_trading_holiday() -> None:
    from app.services import aniu_service as aniu_service_module

    shanghai = ZoneInfo("Asia/Shanghai")
    start = datetime(2026, 10, 1, 8, 0, tzinfo=shanghai)

    original_is_trading_day = aniu_service_module.trading_calendar_service.is_trading_day
    original_next_trading_day = (
        aniu_service_module.trading_calendar_service.next_trading_day
    )
    holiday_break = {
        "2026-10-01",
        "2026-10-02",
        "2026-10-05",
        "2026-10-06",
        "2026-10-07",
        "2026-10-08",
    }
    aniu_service_module.trading_calendar_service.is_trading_day = lambda current: (
        current.isoformat() not in holiday_break and current.weekday() < 5
    )
    aniu_service_module.trading_calendar_service.next_trading_day = lambda current: date(
        2026, 10, 9
    )

    try:
        result = aniu_service._compute_next_run_at("0 8 * * 1-5", from_time=start)
    finally:
        aniu_service_module.trading_calendar_service.is_trading_day = (
            original_is_trading_day
        )
        aniu_service_module.trading_calendar_service.next_trading_day = (
            original_next_trading_day
        )

    assert result is not None
    assert result.date().isoformat() == "2026-10-09"


def test_process_due_schedule_recomputes_non_trading_due_task_without_running(
    monkeypatch, tmp_path
) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    init_db()
    shanghai = ZoneInfo("Asia/Shanghai")

    with session_scope() as db:
        schedule = StrategySchedule(
            name="测试非交易日任务",
            cron_expression="0 8 * * 1-5",
            task_prompt="test",
            timeout_seconds=1800,
            enabled=True,
            next_run_at=datetime(2026, 10, 1, 0, 0),
        )
        db.add(schedule)
        db.flush()
        schedule_id = schedule.id

    from app.services import aniu_service as aniu_service_module

    original_now_shanghai = aniu_service_module.now_shanghai
    original_is_trading_day = aniu_service_module.trading_calendar_service.is_trading_day
    original_next_trading_day = (
        aniu_service_module.trading_calendar_service.next_trading_day
    )
    holiday_break = {
        "2026-10-01",
        "2026-10-02",
        "2026-10-05",
        "2026-10-06",
        "2026-10-07",
        "2026-10-08",
    }
    aniu_service_module.now_shanghai = lambda: datetime(
        2026, 10, 1, 8, 1, tzinfo=shanghai
    )
    aniu_service_module.trading_calendar_service.is_trading_day = lambda current: (
        current.isoformat() not in holiday_break and current.weekday() < 5
    )
    aniu_service_module.trading_calendar_service.next_trading_day = lambda current: date(
        2026, 10, 9
    )
    try:
        aniu_service.process_due_schedule()
    finally:
        aniu_service_module.now_shanghai = original_now_shanghai
        aniu_service_module.trading_calendar_service.is_trading_day = (
            original_is_trading_day
        )
        aniu_service_module.trading_calendar_service.next_trading_day = (
            original_next_trading_day
        )
        _reset_db_state()

    with session_scope() as db:
        saved = db.get(StrategySchedule, schedule_id)
        assert saved is not None
        assert saved.last_run_at is None
        assert saved.next_run_at is not None
        assert saved.next_run_at.date().isoformat() == "2026-10-09"


def test_execute_run_failure_advances_schedule_window(monkeypatch, tmp_path) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    init_db()
    shanghai = ZoneInfo("Asia/Shanghai")

    with session_scope() as db:
        schedule = StrategySchedule(
            name="收盘分析",
            cron_expression="30 15 * * 1-5",
            task_prompt="test",
            timeout_seconds=1800,
            enabled=True,
            next_run_at=datetime(2026, 4, 13, 7, 30, tzinfo=timezone.utc),
        )
        db.add(schedule)
        db.flush()
        schedule_id = schedule.id

    from app.services import aniu_service as aniu_service_module

    original_now_shanghai = aniu_service_module.now_shanghai
    monkeypatch.setattr(
        aniu_service_module,
        "now_shanghai",
        lambda: datetime(2026, 4, 13, 15, 31, tzinfo=shanghai),
    )
    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "get_or_create_settings",
        lambda db: type("StubSettings", (), {
            "id": 1,
            "mx_api_key": "demo-key",
            "llm_base_url": "https://example.com/v1",
            "llm_api_key": "token",
            "llm_model": "demo-model",
            "system_prompt": "prompt",
            "timeout_seconds": 1800,
        })(),
    )
    monkeypatch.setattr(
        aniu_service_module.llm_service,
        "run_agent_with_messages",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        aniu_service_module,
        "now_utc",
        lambda: datetime(2026, 4, 13, 7, 31, tzinfo=timezone.utc),
    )

    try:
        try:
            aniu_service.execute_run(trigger_source="schedule", schedule_id=schedule_id)
        except RuntimeError as exc:
            assert str(exc) == "boom"
    finally:
        aniu_service_module.now_shanghai = original_now_shanghai
        _reset_db_state()

    with session_scope() as db:
        saved = db.get(StrategySchedule, schedule_id)
        assert saved is not None
        assert saved.last_run_at is not None
        assert saved.next_run_at is not None
        assert saved.retry_count == 1
        assert saved.retry_after_at is not None
        next_run = saved.next_run_at
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)
        retry_after = saved.retry_after_at
        if retry_after is not None and retry_after.tzinfo is None:
            retry_after = retry_after.replace(tzinfo=timezone.utc)
        next_run_shanghai = next_run.astimezone(shanghai)
        assert next_run_shanghai.date().isoformat() == "2026-04-14"
        assert next_run_shanghai.hour == 15
        assert next_run_shanghai.minute == 30
        assert retry_after is not None
        assert retry_after.isoformat() == "2026-04-13T07:36:00+00:00"


def test_execute_run_failure_stops_retry_after_third_retry(monkeypatch, tmp_path) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    init_db()
    shanghai = ZoneInfo("Asia/Shanghai")

    with session_scope() as db:
        schedule = StrategySchedule(
            name="收盘分析",
            cron_expression="30 15 * * 1-5",
            task_prompt="test",
            timeout_seconds=1800,
            enabled=True,
            retry_count=3,
            retry_after_at=datetime(2026, 4, 13, 7, 20, tzinfo=timezone.utc),
            next_run_at=datetime(2026, 4, 13, 7, 30, tzinfo=timezone.utc),
        )
        db.add(schedule)
        db.flush()
        schedule_id = schedule.id

    from app.services import aniu_service as aniu_service_module

    original_now_shanghai = aniu_service_module.now_shanghai
    monkeypatch.setattr(
        aniu_service_module,
        "now_shanghai",
        lambda: datetime(2026, 4, 13, 15, 31, tzinfo=shanghai),
    )
    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "get_or_create_settings",
        lambda db: type("StubSettings", (), {
            "id": 1,
            "mx_api_key": "demo-key",
            "llm_base_url": "https://example.com/v1",
            "llm_api_key": "token",
            "llm_model": "demo-model",
            "system_prompt": "prompt",
            "timeout_seconds": 1800,
        })(),
    )
    monkeypatch.setattr(
        aniu_service_module.llm_service,
        "run_agent_with_messages",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        aniu_service_module,
        "now_utc",
        lambda: datetime(2026, 4, 13, 7, 31, tzinfo=timezone.utc),
    )

    try:
        try:
            aniu_service.execute_run(trigger_source="schedule", schedule_id=schedule_id)
        except RuntimeError as exc:
            assert str(exc) == "boom"
    finally:
        aniu_service_module.now_shanghai = original_now_shanghai
        _reset_db_state()

    with session_scope() as db:
        saved = db.get(StrategySchedule, schedule_id)
        assert saved is not None
        assert saved.retry_count == 0
        assert saved.retry_after_at is None


def test_manual_failure_does_not_increment_retry_count(monkeypatch, tmp_path) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    init_db()
    shanghai = ZoneInfo("Asia/Shanghai")

    with session_scope() as db:
        schedule = StrategySchedule(
            name="收盘分析",
            cron_expression="30 15 * * 1-5",
            task_prompt="test",
            timeout_seconds=1800,
            enabled=True,
            retry_count=2,
            retry_after_at=datetime(2026, 4, 13, 7, 20, tzinfo=timezone.utc),
            next_run_at=datetime(2026, 4, 13, 7, 30, tzinfo=timezone.utc),
        )
        db.add(schedule)
        db.flush()
        schedule_id = schedule.id

    from app.services import aniu_service as aniu_service_module

    original_now_shanghai = aniu_service_module.now_shanghai
    monkeypatch.setattr(
        aniu_service_module,
        "now_shanghai",
        lambda: datetime(2026, 4, 13, 15, 31, tzinfo=shanghai),
    )
    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "get_or_create_settings",
        lambda db: type("StubSettings", (), {
            "id": 1,
            "mx_api_key": "demo-key",
            "llm_base_url": "https://example.com/v1",
            "llm_api_key": "token",
            "llm_model": "demo-model",
            "system_prompt": "prompt",
            "timeout_seconds": 1800,
        })(),
    )
    monkeypatch.setattr(
        aniu_service_module.llm_service,
        "run_agent_with_messages",
        lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        aniu_service_module,
        "now_utc",
        lambda: datetime(2026, 4, 13, 7, 31, tzinfo=timezone.utc),
    )

    try:
        try:
            aniu_service.execute_run(trigger_source="manual", schedule_id=schedule_id)
        except RuntimeError as exc:
            assert str(exc) == "boom"
    finally:
        aniu_service_module.now_shanghai = original_now_shanghai
        _reset_db_state()

    with session_scope() as db:
        saved = db.get(StrategySchedule, schedule_id)
        assert saved is not None
        assert saved.retry_count == 2
        assert saved.retry_after_at is not None


def test_process_due_schedule_runs_due_retry_when_window_arrives(monkeypatch, tmp_path) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    init_db()
    shanghai = ZoneInfo("Asia/Shanghai")

    with session_scope() as db:
        retry_schedule = StrategySchedule(
            name="收盘分析",
            cron_expression="30 15 * * 1-5",
            task_prompt="test",
            timeout_seconds=1800,
            enabled=True,
            retry_count=1,
            retry_after_at=datetime(2026, 4, 13, 7, 20, tzinfo=timezone.utc),
            next_run_at=datetime(2026, 4, 14, 7, 30, tzinfo=timezone.utc),
        )
        normal_schedule = StrategySchedule(
            name="盘前分析",
            cron_expression="0 8 * * 1-5",
            task_prompt="test",
            timeout_seconds=1800,
            enabled=True,
            next_run_at=datetime(2026, 4, 14, 0, 0, tzinfo=timezone.utc),
        )
        db.add(retry_schedule)
        db.add(normal_schedule)
        db.flush()
        retry_id = retry_schedule.id
        normal_id = normal_schedule.id

    from app.services import aniu_service as aniu_service_module

    original_now_shanghai = aniu_service_module.now_shanghai
    original_is_trading_day = aniu_service_module.trading_calendar_service.is_trading_day
    called: list[tuple[str, int | None]] = []

    monkeypatch.setattr(
        aniu_service_module,
        "now_shanghai",
        lambda: datetime(2026, 4, 13, 15, 31, tzinfo=shanghai),
    )
    monkeypatch.setattr(
        aniu_service_module.trading_calendar_service,
        "is_trading_day",
        lambda current: True,
    )
    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "execute_run",
        lambda trigger_source="manual", schedule_id=None: called.append((trigger_source, schedule_id)),
    )

    try:
        aniu_service.process_due_schedule()
    finally:
        aniu_service_module.now_shanghai = original_now_shanghai
        aniu_service_module.trading_calendar_service.is_trading_day = original_is_trading_day
        _reset_db_state()

    assert called == [("schedule", retry_id)]
    assert retry_id != normal_id


def test_process_due_schedule_does_not_probe_locked_before_execute(monkeypatch, tmp_path) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    init_db()
    shanghai = ZoneInfo("Asia/Shanghai")

    with session_scope() as db:
        schedule = StrategySchedule(
            name="收盘分析",
            cron_expression="30 15 * * 1-5",
            task_prompt="test",
            timeout_seconds=1800,
            enabled=True,
            next_run_at=datetime(2026, 4, 13, 7, 30, tzinfo=timezone.utc),
        )
        db.add(schedule)
        db.flush()
        schedule_id = schedule.id

    from app.services import aniu_service as aniu_service_module

    original_now_shanghai = aniu_service_module.now_shanghai
    original_is_trading_day = aniu_service_module.trading_calendar_service.is_trading_day
    original_lock = aniu_service_module.aniu_service._run_lock
    called: list[tuple[str, int | None]] = []

    class LockProbe:
        def locked(self) -> bool:
            raise AssertionError("process_due_schedule should not call locked()")

    monkeypatch.setattr(
        aniu_service_module,
        "now_shanghai",
        lambda: datetime(2026, 4, 13, 15, 31, tzinfo=shanghai),
    )
    monkeypatch.setattr(
        aniu_service_module.trading_calendar_service,
        "is_trading_day",
        lambda current: True,
    )
    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "execute_run",
        lambda trigger_source="manual", schedule_id=None: called.append((trigger_source, schedule_id)),
    )
    aniu_service_module.aniu_service._run_lock = LockProbe()

    try:
        aniu_service.process_due_schedule()
    finally:
        aniu_service_module.now_shanghai = original_now_shanghai
        aniu_service_module.trading_calendar_service.is_trading_day = original_is_trading_day
        aniu_service_module.aniu_service._run_lock = original_lock
        _reset_db_state()

    assert called == [("schedule", schedule_id)]


def test_execute_run_rolls_back_partial_trade_orders_when_order_persist_fails(
    monkeypatch, tmp_path
) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    init_db()

    from app.services import aniu_service as aniu_service_module

    class StubClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def close(self) -> None:
            pass

    def fake_run_agent_with_messages(*, app_settings, client, messages, emit=None):
        del app_settings, client, messages, emit
        return (
            {
                "final_answer": "执行两笔交易",
                "tool_calls": [
                    {
                        "name": "mx_moni_trade",
                        "result": {
                            "ok": True,
                            "executed_action": {
                                "action": "BUY",
                                "symbol": "300059",
                                "quantity": 100,
                                "price_type": "MARKET",
                            },
                            "result": {"order_id": "A-1"},
                        },
                    },
                    {
                        "name": "mx_moni_trade",
                        "result": {
                            "ok": True,
                            "executed_action": {
                                "action": "SELL",
                                "symbol": "600519",
                                "quantity": 50,
                                "price_type": "LIMIT",
                                "price": 123.45,
                            },
                            "result": {"order_id": "A-2"},
                        },
                    },
                ],
            },
            {"messages": []},
            {"responses": []},
            {"messages": []},
        )

    real_session_scope = aniu_service_module.session_scope
    trade_order_add_count = {"value": 0}

    @contextmanager
    def flaky_session_scope():
        with real_session_scope() as db:
            original_add = db.add

            def flaky_add(instance):
                if isinstance(instance, TradeOrder):
                    trade_order_add_count["value"] += 1
                    if trade_order_add_count["value"] == 2:
                        raise RuntimeError("persist order boom")
                return original_add(instance)

            db.add = flaky_add  # type: ignore[method-assign]
            yield db

    monkeypatch.setattr(aniu_service_module, "MXClient", StubClient)
    monkeypatch.setattr(
        aniu_service_module.llm_service,
        "run_agent_with_messages",
        fake_run_agent_with_messages,
    )
    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "get_or_create_settings",
        lambda db: type(
            "StubSettings",
            (),
            {
                "id": 1,
                "mx_api_key": "demo-key",
                "llm_base_url": "https://example.com/v1",
                "llm_api_key": "token",
                "llm_model": "demo-model",
                "system_prompt": "prompt",
                "timeout_seconds": 1800,
                "task_prompt": "请执行测试交易。",
            },
        )(),
    )
    monkeypatch.setattr(aniu_service_module, "session_scope", flaky_session_scope)

    try:
        with pytest.raises(RuntimeError, match="persist order boom"):
            aniu_service.execute_run(trigger_source="manual")

        with session_scope() as db:
            runs = db.query(StrategyRun).all()
            orders = db.query(TradeOrder).all()
    finally:
        _reset_db_state()

    assert len(runs) == 1
    assert runs[0].status == "failed"
    assert orders == []


def test_trading_calendar_service_parses_szse_month_payload(monkeypatch, tmp_path) -> None:
    from app.services.trading_calendar_service import TradingCalendarService

    service = TradingCalendarService()
    service._data_path = tmp_path / "trading_calendar.json"

    payload = {
        "data": [
            {"zrxh": 6, "jybz": "1", "jyrq": "2026-08-07"},
            {"zrxh": 7, "jybz": "0", "jyrq": "2026-08-08"},
            {"zrxh": 1, "jybz": "0", "jyrq": "2026-08-09"},
            {"zrxh": 2, "jybz": "1", "jyrq": "2026-08-10"},
        ],
        "nowdate": "2026-07-31",
    }

    captured = {}

    class FakeResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return FakeResponse(payload)

    monkeypatch.setattr("app.services.trading_calendar_service.httpx.get", fake_get)

    result = service._fetch_month_once(2026, 8)

    assert result == ["2026-08-07", "2026-08-10"]
    assert captured["url"] == "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
    assert captured["params"] == {"month": "2026-08"}


def test_trading_calendar_service_empty_data_is_success_not_retried(monkeypatch, tmp_path) -> None:
    from app.services.trading_calendar_service import TradingCalendarService

    service = TradingCalendarService()
    service._data_path = tmp_path / "trading_calendar.json"
    calls = {"n": 0}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [], "nowdate": "2026-07-31"}

    def fake_get(url, params=None, headers=None, timeout=None):
        calls["n"] += 1
        return FakeResponse()

    monkeypatch.setattr("app.services.trading_calendar_service.httpx.get", fake_get)

    result = service._fetch_month_once(2027, 1)
    assert result == []
    assert calls["n"] == 1  # empty data not retried


def test_trading_calendar_service_retries_remote_fetch_until_success(monkeypatch) -> None:
    from app.services.trading_calendar_service import TradingCalendarService

    service = TradingCalendarService()
    attempts: list[tuple[int, int]] = []

    def fake_fetch_once(year: int, month: int) -> list[str]:
        attempts.append((year, month))
        if len(attempts) < 3:
            raise RuntimeError("temporary failure")
        return ["2027-01-04"]

    service._fetch_month_once = fake_fetch_once  # type: ignore[method-assign]

    result = service._fetch_month(2027, 1)

    assert result == ["2027-01-04"]
    assert attempts == [(2027, 1), (2027, 1), (2027, 1)]


def test_trading_calendar_service_raises_after_retry_limit(monkeypatch) -> None:
    from app.services.trading_calendar_service import TradingCalendarService

    service = TradingCalendarService()
    attempts: list[tuple[int, int]] = []

    def fake_fetch_once(year: int, month: int) -> list[str]:
        attempts.append((year, month))
        raise RuntimeError("temporary failure")

    service._fetch_month_once = fake_fetch_once  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="已重试 3 次仍失败"):
        service._fetch_month(2027, 1)

    assert attempts == [(2027, 1), (2027, 1), (2027, 1), (2027, 1)]


def test_trading_calendar_service_ensure_month_merges_and_is_idempotent(monkeypatch, tmp_path) -> None:
    from app.services.trading_calendar_service import TradingCalendarService

    service = TradingCalendarService()
    service._data_path = tmp_path / "trading_calendar.json"
    service._calendar = {"version": 1, "source": "szse_trade_cal", "years": {}}

    calls = {"n": 0}

    def fake_fetch(year: int, month: int) -> list[str]:
        calls["n"] += 1
        return ["2027-01-04", "2027-01-05"]

    monkeypatch.setattr(service, "_fetch_month", fake_fetch)

    service.ensure_month(2027, 1)
    assert service._calendar["years"]["2027"]["trading_days"] == ["2027-01-04", "2027-01-05"]
    assert service._data_path.exists()

    # Second call: month already has data -> no fetch.
    service.ensure_month(2027, 1)
    assert calls["n"] == 1


def test_trading_calendar_service_ensure_month_throttles_failures_per_day(monkeypatch, tmp_path) -> None:
    from app.services.trading_calendar_service import TradingCalendarService
    from datetime import date as date_cls

    service = TradingCalendarService()
    service._data_path = tmp_path / "trading_calendar.json"
    service._calendar = {"version": 1, "source": "szse_trade_cal", "years": {}}

    calls = {"n": 0}

    def fake_fetch(year: int, month: int) -> list[str]:
        calls["n"] += 1
        raise RuntimeError("boom")

    monkeypatch.setattr(service, "_fetch_month", fake_fetch)

    service.ensure_month(2027, 1)
    assert calls["n"] == 1
    # Same day -> throttled, no new attempt.
    service.ensure_month(2027, 1)
    assert calls["n"] == 1
    # Next day -> retry allowed.
    service._month_last_attempt[(2027, 1)] = date_cls(2020, 1, 1)
    service.ensure_month(2027, 1)
    assert calls["n"] == 2


def test_trading_calendar_service_weekday_fallback_when_no_data(monkeypatch, tmp_path) -> None:
    from datetime import date as date_cls
    from app.services.trading_calendar_service import TradingCalendarService

    service = TradingCalendarService()
    service._data_path = tmp_path / "trading_calendar.json"
    service._calendar = {"version": 1, "source": "szse_trade_cal", "years": {}}

    def fake_fetch(year: int, month: int) -> list[str]:
        raise RuntimeError("unavailable")

    monkeypatch.setattr(service, "_fetch_month", fake_fetch)

    # Monday 2026-08-03 -> fallback True (no real data for the month).
    assert service.is_trading_day(date_cls(2026, 8, 3)) is True
    # Saturday 2026-08-08 -> fallback False.
    assert service.is_trading_day(date_cls(2026, 8, 8)) is False
    # Cache not polluted: trading_days still empty (next day could retry).
    assert service._calendar["years"].get("2026", {}).get("trading_days") in (None, [])


def test_trading_calendar_service_real_data_overrides_fallback(monkeypatch, tmp_path) -> None:
    from datetime import date as date_cls
    from app.services.trading_calendar_service import TradingCalendarService

    service = TradingCalendarService()
    service._data_path = tmp_path / "trading_calendar.json"
    service._calendar = {"version": 1, "source": "szse_trade_cal", "years": {}}

    def fake_fetch(year: int, month: int) -> list[str]:
        return ["2026-08-03"]  # only Aug 3 is a trading day

    monkeypatch.setattr(service, "_fetch_month", fake_fetch)

    assert service.is_trading_day(date_cls(2026, 8, 3)) is True
    # Saturday has real data for the month -> genuine non-trading day.
    assert service.is_trading_day(date_cls(2026, 8, 8)) is False


def test_trading_calendar_service_legacy_year_cache_is_reused(monkeypatch, tmp_path) -> None:
    from app.services.trading_calendar_service import TradingCalendarService

    service = TradingCalendarService()
    service._data_path = tmp_path / "trading_calendar.json"
    # Legacy cache: no cached_months marker, full-year data from old source.
    service._calendar = {
        "version": 1,
        "source": "codebuddy_trade_cal",
        "years": {"2026": {"trading_days": ["2026-08-04", "2026-08-05"]}},
    }

    calls = {"n": 0}

    def fake_fetch(year: int, month: int) -> list[str]:
        calls["n"] += 1
        return ["2026-08-06"]

    monkeypatch.setattr(service, "_fetch_month", fake_fetch)

    # August 2026 already has data -> skipped, no fetch.
    service.ensure_month(2026, 8)
    assert calls["n"] == 0
    assert service.is_trading_day(__import__("datetime").date(2026, 8, 4)) is True


def test_trading_calendar_service_ensure_months_batch(monkeypatch, tmp_path) -> None:
    from app.services.trading_calendar_service import TradingCalendarService

    service = TradingCalendarService()
    service._data_path = tmp_path / "trading_calendar.json"
    service._calendar = {"version": 1, "source": "szse_trade_cal", "years": {}}

    seen: list[tuple[int, int]] = []

    def fake_fetch(year: int, month: int) -> list[str]:
        seen.append((year, month))
        return [f"{year}-{month:02d}-05"]

    monkeypatch.setattr(service, "_fetch_month", fake_fetch)

    service.ensure_months(["2026-08", "2026-09", "bad", "2026-13"])
    assert seen == [(2026, 8), (2026, 9)]


def test_order_status_text_derives_from_fill_progress() -> None:
    from app.services.aniu_service import _order_status_text

    assert _order_status_text(2, order_quantity=200, filled_quantity=0) == "已报"
    assert _order_status_text("2", order_quantity=200, filled_quantity=100) == "部分成交"
    assert _order_status_text("2", order_quantity=200, filled_quantity=200) == "已成交"


def test_account_overview_prefers_live_positions_over_cached_snapshot(monkeypatch) -> None:
    from app.services import aniu_service as aniu_service_module

    live_balance = {
        "data": {
            "totalAsset": 120000,
            "stockMarketValue": 5000,
            "balanceActual": 115000,
        }
    }
    cached_balance = {
        "data": {
            "totalAsset": 100000,
            "stockMarketValue": 0,
            "balanceActual": 100000,
        }
    }
    live_positions = {
        "data": {
            "rows": [
                {
                    "stockCode": "300373",
                    "stockName": "扬杰科技",
                    "marketValue": 5000,
                    "count": 100,
                    "availCount": 100,
                }
            ]
        }
    }
    cached_positions = {"data": {"rows": []}}
    live_orders = {
        "data": {
            "rows": [
                {
                    "orderId": "1",
                    "stockCode": "300373",
                    "stockName": "扬杰科技",
                    "orderStatus": 2,
                    "orderDrt": 1,
                }
            ]
        }
    }

    class StubClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_balance(self) -> dict[str, object]:
            return live_balance

        def get_positions(self) -> dict[str, object]:
            return live_positions

        def get_orders(self) -> dict[str, object]:
            return live_orders

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "get_or_create_settings",
        lambda db: type("StubSettings", (), {"mx_api_key": "demo-key"})(),
    )
    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "_get_recent_account_snapshot",
        lambda db: (cached_balance, cached_positions, None),
    )
    monkeypatch.setattr(aniu_service_module, "MXClient", StubClient)

    overview = aniu_service_module.aniu_service.get_account_overview(
        include_raw=True,
        force_refresh=True,
    )

    assert overview["positions"]
    assert overview["positions"][0]["name"] == "扬杰科技"
    assert overview["orders"][0]["status_text"] == "已报"
    assert overview["raw_positions"] == live_positions
    assert overview["raw_balance"] == live_balance


def test_account_overview_falls_back_to_cached_orders_when_live_orders_fail(
    monkeypatch,
) -> None:
    from app.services import aniu_service as aniu_service_module

    live_balance = {
        "data": {
            "totalAsset": 120000,
            "stockMarketValue": 5000,
            "balanceActual": 115000,
        }
    }
    live_positions = {
        "data": {
            "rows": [
                {
                    "stockCode": "300373",
                    "stockName": "扬杰科技",
                    "marketValue": 5000,
                    "count": 100,
                    "availCount": 100,
                }
            ]
        }
    }
    cached_orders = {
        "data": {
            "rows": [
                {
                    "orderId": "cached-order-1",
                    "stockCode": "300373",
                    "stockName": "扬杰科技",
                    "orderStatus": 2,
                    "orderDrt": 1,
                }
            ]
        }
    }

    class StubClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_balance(self) -> dict[str, object]:
            return live_balance

        def get_positions(self) -> dict[str, object]:
            return live_positions

        def get_orders(self) -> dict[str, object]:
            raise RuntimeError("orders boom")

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "get_or_create_settings",
        lambda db: type("StubSettings", (), {"mx_api_key": "demo-key"})(),
    )
    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "_get_recent_account_snapshot",
        lambda db: (None, None, cached_orders),
    )
    monkeypatch.setattr(aniu_service_module, "MXClient", StubClient)

    overview = aniu_service_module.aniu_service.get_account_overview(
        include_raw=True,
        force_refresh=True,
    )

    assert overview["orders"]
    assert overview["orders"][0]["order_id"] == "cached-order-1"
    assert overview["raw_orders"] == cached_orders
    assert any("缓存的委托数据" in message for message in overview["errors"])


def test_recent_account_snapshot_merges_balance_positions_and_orders_from_runs(
    monkeypatch, tmp_path
) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    init_db()

    with session_scope() as db:
        oldest = StrategyRun(
            status="completed",
            skill_payloads={
                "tool_calls": [
                    {
                        "name": "mx_get_orders",
                        "result": {
                            "ok": True,
                            "result": {"data": {"rows": [{"orderId": "A-1"}]}},
                        },
                    }
                ]
            },
        )
        middle = StrategyRun(
            status="completed",
            skill_payloads={
                "tool_calls": [
                    {
                        "name": "mx_get_positions",
                        "result": {
                            "ok": True,
                            "result": {"data": {"rows": [{"stockCode": "300373"}]}},
                        },
                    }
                ]
            },
        )
        latest = StrategyRun(
            status="completed",
            skill_payloads={
                "tool_calls": [
                    {
                        "name": "mx_get_balance",
                        "result": {
                            "ok": True,
                            "result": {"data": {"totalAsset": 100000}},
                        },
                    }
                ]
            },
        )
        db.add_all([oldest, middle, latest])

    with session_scope() as db:
        balance, positions, orders = aniu_service._get_recent_account_snapshot(db)

    _reset_db_state()

    assert balance == {"data": {"totalAsset": 100000}}
    assert positions == {"data": {"rows": [{"stockCode": "300373"}]}}
    assert orders == {"data": {"rows": [{"orderId": "A-1"}]}}


def test_execute_run_does_not_prefetch_account_before_agent(monkeypatch, tmp_path) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    init_db()

    from app.services import aniu_service as aniu_service_module

    captured_task_prompt: dict[str, str] = {}

    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "get_or_create_settings",
        lambda db: type(
            "StubSettings",
            (),
            {
                "id": 1,
                "mx_api_key": "demo-key",
                "llm_base_url": "https://example.com/v1",
                "llm_api_key": "token",
                "llm_model": "demo-model",
                "system_prompt": "prompt",
                "timeout_seconds": 1800,
                "task_prompt": "请分析当前账户。",
            },
        )(),
    )

    class StubClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def get_balance(self) -> dict[str, object]:
            raise AssertionError("run path should not prefetch balance")
            return {"data": {"totalAsset": 100000, "balanceActual": 95000}}

        def get_positions(self) -> dict[str, object]:
            raise AssertionError("run path should not prefetch positions")
            return {
                "data": {
                    "rows": [
                        {
                            "stockCode": "300373",
                            "stockName": "扬杰科技",
                            "marketValue": 5000,
                            "count": 100,
                        }
                    ]
                }
            }

        def get_orders(self) -> dict[str, object]:
            raise AssertionError("run path should not prefetch orders")
            return {
                "data": {
                    "rows": [
                        {
                            "orderId": "1",
                            "stockCode": "300373",
                            "stockName": "扬杰科技",
                            "orderStatus": 2,
                            "orderDrt": 1,
                        }
                    ]
                }
            }

        def close(self) -> None:
            pass

    def fake_run_agent_with_messages(*, app_settings, client, messages, emit=None):
        del client, emit
        captured_task_prompt["value"] = app_settings.task_prompt
        captured_task_prompt["messages"] = messages
        return (
            {
                "final_answer": "保持观察。",
                "tool_calls": [],
            },
            {"messages": []},
            {"responses": []},
            {"messages": []},
        )

    monkeypatch.setattr(aniu_service_module, "MXClient", StubClient)
    monkeypatch.setattr(
        aniu_service_module.llm_service,
        "run_agent_with_messages",
        fake_run_agent_with_messages,
    )

    run = aniu_service.execute_run(trigger_source="manual")

    _reset_db_state()

    assert captured_task_prompt["value"] == "请分析当前账户。"
    assert any(msg.get("role") == "user" for msg in captured_task_prompt["messages"])
    assert run.skill_payloads is not None
    assert run.skill_payloads.get("prefetched_tool_calls") in (None, [])
    assert run.skill_payloads.get("prefetched_context") in (None, "")


def test_execute_run_passes_emit_when_run_agent_supports_it(monkeypatch, tmp_path) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    init_db()

    from app.services import aniu_service as aniu_service_module

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "get_or_create_settings",
        lambda db: type(
            "StubSettings",
            (),
            {
                "id": 1,
                "mx_api_key": "demo-key",
                "llm_base_url": "https://example.com/v1",
                "llm_api_key": "token",
                "llm_model": "demo-model",
                "system_prompt": "prompt",
                "timeout_seconds": 1800,
                "task_prompt": "请分析当前账户。",
            },
        )(),
    )

    class StubClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def close(self) -> None:
            pass

    def fake_run_agent_with_messages(*, app_settings, client, messages, emit=None):
        del client, messages
        captured["task_prompt"] = app_settings.task_prompt
        captured["emit_is_callable"] = callable(emit)
        return (
            {
                "final_answer": "保持观察。",
                "tool_calls": [],
            },
            {"messages": []},
            {"responses": []},
            {"messages": []},
        )

    monkeypatch.setattr(aniu_service_module, "MXClient", StubClient)
    monkeypatch.setattr(
        aniu_service_module.llm_service,
        "run_agent_with_messages",
        fake_run_agent_with_messages,
    )

    run = aniu_service.execute_run(trigger_source="manual")

    _reset_db_state()

    assert run.status == "completed"
    assert captured["task_prompt"] == "请分析当前账户。"
    assert captured["emit_is_callable"] is True


def test_manual_trade_run_overrides_default_run_type(monkeypatch, tmp_path) -> None:
    _use_temp_db(monkeypatch, tmp_path)
    init_db()

    from app.services import aniu_service as aniu_service_module

    captured: dict[str, object] = {}

    monkeypatch.setattr(
        aniu_service_module.aniu_service,
        "get_or_create_settings",
        lambda db: type(
            "StubSettings",
            (),
            {
                "id": 1,
                "mx_api_key": "demo-key",
                "llm_base_url": "https://example.com/v1",
                "llm_api_key": "token",
                "llm_model": "demo-model",
                "system_prompt": "prompt",
                "timeout_seconds": 1800,
                "task_prompt": "请分析当前账户。",
            },
        )(),
    )

    class StubClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def close(self) -> None:
            pass

    def fake_run_agent_with_messages(*, app_settings, client, messages, emit=None):
        del client, messages, emit
        captured["run_type"] = app_settings.run_type
        captured["task_prompt"] = app_settings.task_prompt
        return (
            {
                "final_answer": "执行交易。",
                "tool_calls": [],
            },
            {"messages": []},
            {"responses": []},
            {"messages": []},
        )

    monkeypatch.setattr(aniu_service_module, "MXClient", StubClient)
    monkeypatch.setattr(
        aniu_service_module.llm_service,
        "run_agent_with_messages",
        fake_run_agent_with_messages,
    )

    run = aniu_service.execute_run(trigger_source="manual", manual_run_type="trade")

    _reset_db_state()

    assert run.status == "completed"
    assert run.run_type == "trade"
    assert captured["run_type"] == "trade"
    assert "生成交易决策" in str(captured["task_prompt"])


def test_daily_profit_trade_date_falls_back_to_previous_trading_day_on_weekend(
    monkeypatch,
) -> None:
    from app.services import aniu_service as aniu_service_module

    shanghai = ZoneInfo("Asia/Shanghai")
    original_now_shanghai = aniu_service_module.now_shanghai
    aniu_service_module.now_shanghai = lambda: datetime(
        2026, 4, 12, 12, 0, tzinfo=shanghai
    )
    try:
        overview = aniu_service._build_account_overview(None, {"data": {"rows": []}})
    finally:
        aniu_service_module.now_shanghai = original_now_shanghai

    assert overview["daily_profit_trade_date"] == "2026-04-10"
