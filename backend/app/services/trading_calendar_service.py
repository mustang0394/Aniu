from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx

_MAX_PROBE_DAYS = 30
_CALENDAR_FETCH_RETRIES = 3
_CALENDAR_SOURCE = "szse_trade_cal"
_CALENDAR_API_URL = "https://www.szse.cn/api/report/exchange/onepersistenthour/monthList"
_CALENDAR_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.szse.cn/",
    "Accept": "application/json, text/plain, */*",
}


class TradingCalendarService:
    def __init__(self) -> None:
        self._data_path = (
            Path(__file__).resolve().parents[1] / "data" / "trading_calendar.json"
        )
        self._calendar: dict[str, object] | None = None
        self._year_days_cache: dict[int, set[str]] = {}
        # Tracks the last calendar day on which a (year, month) was attempted
        # via the remote API. Used to throttle retries to once per day when the
        # remote returns no data (e.g. unannounced future month) or errors out.
        self._month_last_attempt: dict[tuple[int, int], date] = {}

    def _load_calendar(self) -> dict[str, object]:
        if self._calendar is None:
            if self._data_path.exists():
                payload = json.loads(self._data_path.read_text(encoding="utf-8"))
                if "years" not in payload:
                    trading_days = payload.get("trading_days", [])
                    by_year: dict[str, list[str]] = {}
                    for item in trading_days:
                        key = str(item)[:4]
                        by_year.setdefault(key, []).append(str(item))
                    payload = {
                        "version": 1,
                        "source": _CALENDAR_SOURCE,
                        "years": {
                            year: {"trading_days": days}
                            for year, days in by_year.items()
                        },
                    }
                self._calendar = payload
            else:
                self._calendar = {
                    "version": 1,
                    "source": _CALENDAR_SOURCE,
                    "years": {},
                }
        return self._calendar

    def _save_calendar(self) -> None:
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._data_path.write_text(
            json.dumps(self._calendar, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Remote fetch (per-month from SZSE monthList endpoint).
    # ------------------------------------------------------------------

    def _fetch_month_once(self, year: int, month: int) -> list[str]:
        """Single logical attempt: GET one month from SZSE.

        Returns the list of trading days (YYYY-MM-DD) for the month. An
        unannounced month returns an empty list (success, not an error).
        HTTP / JSON errors raise RuntimeError.
        """
        month_str = f"{year}-{month:02d}"
        try:
            response = httpx.get(
                _CALENDAR_API_URL,
                params={"month": month_str},
                headers=_CALENDAR_HEADERS,
                timeout=30.0,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"交易日历远程接口返回错误 ({exc.response.status_code})"
            ) from exc
        except httpx.TimeoutException as exc:
            raise RuntimeError("交易日历远程接口请求超时") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"交易日历远程接口请求失败: {exc}") from exc

        try:
            result = response.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError("交易日历远程接口返回了无效 JSON") from exc

        if not isinstance(result, dict):
            raise RuntimeError("交易日历远程接口返回结构异常")

        data = result.get("data")
        if not isinstance(data, list):
            raise RuntimeError("交易日历远程接口缺少 data 字段")

        trading_days: list[str] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if str(item.get("jybz") or "").strip() != "1":
                continue
            day = str(item.get("jyrq") or "").strip()
            if len(day) == 10 and day[4] == "-" and day[7] == "-":
                trading_days.append(day)
        return sorted(set(trading_days))

    def _fetch_month(self, year: int, month: int) -> list[str]:
        """Retry transient errors up to ``_CALENDAR_FETCH_RETRIES`` times.

        Empty data (unannounced month) is a success and is not retried.
        """
        last_error: RuntimeError | None = None
        for attempt in range(_CALENDAR_FETCH_RETRIES + 1):
            try:
                return self._fetch_month_once(year, month)
            except RuntimeError as exc:
                last_error = exc
                if attempt == _CALENDAR_FETCH_RETRIES:
                    raise RuntimeError(
                        f"{exc}；已重试 {_CALENDAR_FETCH_RETRIES} 次仍失败"
                    ) from exc

        raise RuntimeError(
            f"missing trading calendar data for {year}-{month:02d}: {last_error}"
        )

    # ------------------------------------------------------------------
    # Cache merge & ensure.
    # ------------------------------------------------------------------

    def _merge_month(self, year: int, new_days: list[str]) -> None:
        calendar = self._load_calendar()
        years_data = calendar.setdefault("years", {})
        if not isinstance(years_data, dict):
            raise RuntimeError("交易日历缓存结构异常")
        key = str(year)
        year_payload = years_data.get(key)
        if not isinstance(year_payload, dict):
            year_payload = {}
            years_data[key] = year_payload
        existing = set(year_payload.get("trading_days") or [])
        existing.update(str(d) for d in new_days)
        year_payload["trading_days"] = sorted(existing)
        calendar["source"] = _CALENDAR_SOURCE
        self._save_calendar()
        self._year_days_cache.pop(year, None)

    def _month_has_data(self, year: int, month: int) -> bool:
        """True if the cache already holds any trading day for this month."""
        days = self._year_days(year)
        prefix = f"{year}-{month:02d}-"
        return any(d.startswith(prefix) for d in days)

    def ensure_month(self, year: int, month: int) -> None:
        """Ensure trading-day data for the given month is cached.

        If the month already has cached data, do nothing. Otherwise fetch from
        the remote API, but throttle retries to once per calendar day: if an
        attempt failed or returned empty today, skip further attempts today
        (the weekday fallback in ``is_trading_day`` keeps scheduling working).
        """
        if self._month_has_data(year, month):
            return
        today = date.today()
        last = self._month_last_attempt.get((year, month))
        if last == today:
            return
        try:
            new_days = self._fetch_month(year, month)
        except Exception:
            self._month_last_attempt[(year, month)] = today
            return
        self._month_last_attempt[(year, month)] = today
        if new_days:
            self._merge_month(year, new_days)

    def ensure_months(self, month_keys: list[str]) -> None:
        for key in month_keys:
            parts = str(key).split("-")
            if len(parts) != 2:
                continue
            try:
                year = int(parts[0])
                month = int(parts[1])
            except ValueError:
                continue
            if not (1 <= month <= 12):
                continue
            self.ensure_month(year, month)

    # Compatibility shims for older callers/tests that monkeypatch or invoke
    # ensure_years/warm_up_years. New code uses ensure_month/ensure_months.
    def ensure_years(self, years: list[int]) -> None:
        for year in years:
            for month in range(1, 13):
                self.ensure_month(year, month)

    def warm_up_years(self, current_year: int) -> None:
        today = date.today()
        self.ensure_month(today.year, today.month)
        next_month_date = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        self.ensure_month(next_month_date.year, next_month_date.month)

    def _year_days(self, year: int) -> set[str]:
        if year in self._year_days_cache:
            return self._year_days_cache[year]
        calendar = self._load_calendar()
        years_data = calendar.get("years", {})
        year_payload = (
            years_data.get(str(year), {}) if isinstance(years_data, dict) else {}
        )
        if not isinstance(year_payload, dict):
            result: set[str] = set()
        else:
            trading_days = year_payload.get("trading_days", [])
            result = {str(item) for item in trading_days}
        self._year_days_cache[year] = result
        return result

    def is_trading_day(self, current: date) -> bool:
        self.ensure_month(current.year, current.month)
        days = self._year_days(current.year)
        if current.isoformat() in days:
            return True
        # The day is not in cached real data. If the month has any real data,
        # this day is a genuine non-trading day. Otherwise (remote returned no
        # data for this month / fetch failed) fall back to weekday check so
        # scheduling is never blocked by missing calendar data.
        if self._month_has_data(current.year, current.month):
            return False
        return current.weekday() < 5

    def next_trading_day(self, current: date) -> date:
        probe = current
        for _ in range(_MAX_PROBE_DAYS):
            if self.is_trading_day(probe):
                return probe
            probe += timedelta(days=1)
        raise RuntimeError(
            f"在 {current} 之后 {_MAX_PROBE_DAYS} 天内未找到交易日，请检查交易日历数据。"
        )


trading_calendar_service = TradingCalendarService()
