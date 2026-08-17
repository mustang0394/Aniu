"""UZI 上游更新的主服务并发门禁测试。"""
from __future__ import annotations

import pytest

from app.services.uzi_report_service import ERROR_UPDATE_BUSY, UziReportService


class _Worker:
    def __init__(self) -> None:
        self.update_calls = 0

    def update_source(self):
        self.update_calls += 1
        return {
            "repository": "https://github.com/wbh604/UZI-Skill",
            "current_commit": "8" * 40,
        }


def test_source_update_is_rejected_while_reports_are_active(monkeypatch) -> None:
    worker = _Worker()
    service = UziReportService(worker_client=worker)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_count_active", lambda _db: 1)

    with pytest.raises(RuntimeError) as exc:
        service.update_source(object())

    assert exc.value.args[0] == ERROR_UPDATE_BUSY
    assert worker.update_calls == 0


def test_source_update_releases_gate_after_worker_failure(monkeypatch) -> None:
    worker = _Worker()
    service = UziReportService(worker_client=worker)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "_count_active", lambda _db: 0)
    monkeypatch.setattr(worker, "update_source", lambda: None)

    with pytest.raises(RuntimeError):
        service.update_source(object())

    assert service._source_update_in_progress is False
