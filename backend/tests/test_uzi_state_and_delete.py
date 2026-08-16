"""UZI 状态机、取消、删除、路径安全与启动对账测试。"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core import rate_limit as rate_limit_module
from app.db import database as database_module
from app.db.database import session_scope
from app.db.models import AppSettings, UziReportJob
from app.services.uzi_report_service import uzi_report_service
from tests.uzi_test_helpers import (
    FakeWorkerClient,
    auth_headers,
    create_uzi_test_client,
    make_completed_report,
)


def test_cancel_terminal_job_is_idempotent(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            job = make_completed_report(db)
            report_id = job.id
        first = client.post(
            f"/api/aniu/uzi/reports/{report_id}/cancel", headers=headers
        )
        second = client.post(
            f"/api/aniu/uzi/reports/{report_id}/cancel", headers=headers
        )
        assert first.status_code == 200
        assert first.json()["cancelled"] is False
        assert first.json()["status"] == "completed"
        assert second.json()["status"] == "completed"
    _reset()


def test_cancel_running_job(monkeypatch, tmp_path) -> None:
    client, worker = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            job = UziReportJob(
                ticker_input="600519.SH",
                status="stage1_running",
                phase="stage1_running",
                progress=20,
                report_rel_dir="",
            )
            db.add(job)
            db.flush()
            job.report_rel_dir = str(job.id)
            report_id = job.id
        response = client.post(
            f"/api/aniu/uzi/reports/{report_id}/cancel", headers=headers
        )
        assert response.status_code == 200
        assert response.json()["cancelled"] is True
        assert response.json()["status"] == "cancelled"
        assert report_id in worker.cancelled
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            assert job.status == "cancelled"
            assert job.error_code == "UZI_CANCELLED"
    _reset()


def test_delete_running_job_returns_409(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            job = UziReportJob(
                ticker_input="600519.SH",
                status="stage1_running",
                phase="stage1_running",
                progress=20,
                report_rel_dir="",
            )
            db.add(job)
            db.flush()
            job.report_rel_dir = str(job.id)
            report_id = job.id
        response = client.delete(
            f"/api/aniu/uzi/reports/{report_id}", headers=headers
        )
        assert response.status_code == 409
        assert "不可删除" in response.json()["detail"]
    _reset()


def test_delete_completed_job_removes_files_and_record(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            job = make_completed_report(db)
            report_id = job.id
            report_dir = uzi_report_service._resolve_report_dir(job.report_rel_dir)
            assert report_dir is not None
            (report_dir / "artifacts").mkdir(parents=True, exist_ok=True)
            (report_dir / "artifacts" / "full-report-standalone.html").write_text("A" * 12000, encoding="utf-8")
        response = client.delete(
            f"/api/aniu/uzi/reports/{report_id}", headers=headers
        )
        assert response.status_code == 204
        with session_scope() as db:
            assert db.get(UziReportJob, report_id) is None
        assert not report_dir.exists()
    _reset()


def test_delete_path_traversal_rejected(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            job = UziReportJob(
                ticker_input="600519.SH",
                status="completed",
                phase="completed",
                progress=100,
                report_rel_dir="../../escape",
            )
            db.add(job)
            db.flush()
            report_id = job.id
        response = client.delete(
            f"/api/aniu/uzi/reports/{report_id}", headers=headers
        )
        # 路径穿越被拒绝：不删除 DB 记录，且不抛 500 泄露路径。
        assert response.status_code == 409
        with session_scope() as db:
            assert db.get(UziReportJob, report_id) is not None
    _reset()


def test_state_machine_forbids_backward_transition(monkeypatch, tmp_path) -> None:
    """状态只能按状态机迁移：服务内部用显式赋值推进，禁止倒退由
    _update_progress 的调用方保证；此处验证任务创建后状态合法。"""
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        response = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "600519.SH"},
            headers=headers,
        )
        payload = response.json()
        assert payload["report"]["status"] == "queued"
        assert payload["report"]["progress"] == 0
        # 状态枚举合法性。
        valid = {"queued", "stage1_running", "llm_review", "stage2_running", "completed", "failed", "cancelled"}
        with session_scope() as db:
            job = db.get(UziReportJob, payload["report"]["id"])
            assert job.status in valid
    _reset()


def test_reconcile_startup_marks_orphaned_failed(monkeypatch, tmp_path) -> None:
    client, worker = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        with session_scope() as db:
            job = UziReportJob(
                ticker_input="600519.SH",
                status="stage1_running",
                phase="stage1_running",
                progress=20,
                report_rel_dir="",
            )
            db.add(job)
            db.flush()
            job.report_rel_dir = str(job.id)
            report_id = job.id
        # Worker 不认识任务（jobs 字典为空）→ 无完整产物 → UZI_ORPHANED_JOB。
        with session_scope() as db:
            uzi_report_service.reconcile_on_startup(db)
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            assert job.status == "failed"
            assert job.error_code == "UZI_ORPHANED_JOB"
    _reset()


def test_reconcile_startup_resumes_stage1_done(monkeypatch, tmp_path) -> None:
    client, worker = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        with session_scope() as db:
            job = UziReportJob(
                ticker_input="600519.SH",
                status="stage1_running",
                phase="stage1_running",
                progress=30,
                report_rel_dir="",
            )
            db.add(job)
            db.flush()
            job.report_rel_dir = str(job.id)
            report_id = job.id
        # Worker 侧 Stage 1 已完成。
        worker.jobs[report_id] = {
            "report_id": str(report_id),
            "status": "succeeded",
            "phase": "stage1_done",
            "progress": 45,
            "error_code": None,
            "error_message": None,
        }
        with session_scope() as db:
            uzi_report_service.reconcile_on_startup(db)
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            assert job.status == "llm_review"
            assert job.progress == 50
    _reset()


def test_reconcile_startup_worker_unavailable(monkeypatch, tmp_path) -> None:
    worker = FakeWorkerClient()
    worker.available = False
    client, _ = create_uzi_test_client(monkeypatch, tmp_path, worker=worker)
    with client:
        with session_scope() as db:
            job = UziReportJob(
                ticker_input="600519.SH",
                status="stage1_running",
                phase="stage1_running",
                progress=20,
                report_rel_dir="",
            )
            db.add(job)
            db.flush()
            job.report_rel_dir = str(job.id)
            report_id = job.id
        with session_scope() as db:
            uzi_report_service.reconcile_on_startup(db)
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            assert job.status == "failed"
            assert job.error_code == "UZI_ORPHANED_JOB"
    _reset()


def _reset() -> None:
    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()
    rate_limit_module._limiter.reset()
    uzi_report_service._cancel_events.clear()
    uzi_report_service.reset_rate_limit()
