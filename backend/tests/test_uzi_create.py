"""UZI 创建任务测试：LLM 未配置 422、输入校验、复用、队列满。"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core import rate_limit as rate_limit_module
from app.db import database as database_module
from app.db.database import session_scope
from app.db.models import UziReportJob
from app.services.uzi_report_service import uzi_report_service
from tests.uzi_test_helpers import (
    FakeWorkerClient,
    auth_headers,
    create_uzi_test_client,
)


def test_create_llm_not_configured_returns_422(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path, llm_configured=False)
    with client:
        headers = auth_headers(client)
        response = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "600519.SH"},
            headers=headers,
        )
        assert response.status_code == 422
        assert "大模型" in response.json()["detail"]
    _reset()


def test_create_rejects_empty_ticker(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        response = client.post("/api/aniu/uzi/reports", json={"ticker": "   "}, headers=headers)
        assert response.status_code == 422
    _reset()


def test_create_rejects_too_long_ticker(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        response = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "X" * 65},
            headers=headers,
        )
        assert response.status_code == 422
    _reset()


def test_create_rejects_path_separator(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        response = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "600519.SH/../../etc"},
            headers=headers,
        )
        assert response.status_code == 422
    _reset()


def test_create_rejects_control_char(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        response = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "600519\x00SH"},
            headers=headers,
        )
        assert response.status_code == 422
    _reset()


def test_create_success_returns_202(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        response = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "600519.SH"},
            headers=headers,
        )
        assert response.status_code == 202
        payload = response.json()
        assert payload["reused"] is False
        assert payload["report"]["status"] == "queued"
        assert payload["report"]["ticker_input"] == "600519.SH"
    _reset()


def test_create_reuses_active_job(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        first = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "600519.SH"},
            headers=headers,
        ).json()
        second = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "600519.SH"},
            headers=headers,
        ).json()
        assert second["reused"] is True
        assert second["report"]["id"] == first["report"]["id"]
    _reset()


def test_create_queue_full_returns_409(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        # 直接塞满非终态任务（UZI_MAX_ACTIVE=1 + UZI_MAX_QUEUED=3）。
        with session_scope() as db:
            for i in range(4):
                job = UziReportJob(
                    ticker_input=f"60000{i}.SH",
                    status="queued",
                    phase="queued",
                    progress=0,
                    report_rel_dir="",
                )
                db.add(job)
                db.flush()
                job.report_rel_dir = str(job.id)
        response = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "600519.SH"},
            headers=headers,
        )
        assert response.status_code == 409
        assert "队列已满" in response.json()["detail"]
    _reset()


def test_worker_unavailable_returns_503(monkeypatch, tmp_path) -> None:
    worker = FakeWorkerClient()
    worker.available = False
    client, _ = create_uzi_test_client(monkeypatch, tmp_path, worker=worker)
    with client:
        headers = auth_headers(client)
        response = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "600519.SH"},
            headers=headers,
        )
        assert response.status_code == 503
        assert "Worker 不可用" in response.json()["detail"]
    _reset()


def _reset() -> None:
    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()
    rate_limit_module._limiter.reset()
    uzi_report_service._cancel_events.clear()
    uzi_report_service.reset_rate_limit()
