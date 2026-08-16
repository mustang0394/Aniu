"""UZI 列表、鉴权、产物白名单与 SSE 测试。"""

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
    auth_headers,
    create_uzi_test_client,
    make_completed_report,
)


def test_all_public_apis_require_jwt(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        # 无 token 全部 401。
        assert client.get("/api/aniu/uzi/status").status_code == 401
        assert client.get("/api/aniu/uzi/reports").status_code == 401
        assert client.post("/api/aniu/uzi/reports", json={"ticker": "600519.SH"}).status_code == 401
        assert client.get("/api/aniu/uzi/reports/1").status_code == 401
        assert client.get("/api/aniu/uzi/reports/1/events").status_code == 401
        assert client.post("/api/aniu/uzi/reports/1/cancel").status_code == 401
        assert client.delete("/api/aniu/uzi/reports/1").status_code == 401
        assert client.get("/api/aniu/uzi/reports/1/artifacts/html").status_code == 401
    _reset()


def test_status_endpoint(monkeypatch, tmp_path) -> None:
    client, worker = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        response = client.get("/api/aniu/uzi/status", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["enabled"] is True
        assert payload["worker_available"] is True
        assert payload["worker_version"] == "7bc779d"
        assert payload["max_queued"] == 3
    _reset()


def test_status_worker_unavailable(monkeypatch, tmp_path) -> None:
    from tests.uzi_test_helpers import FakeWorkerClient

    worker = FakeWorkerClient()
    worker.available = False
    client, _ = create_uzi_test_client(monkeypatch, tmp_path, worker=worker)
    with client:
        headers = auth_headers(client)
        response = client.get("/api/aniu/uzi/status", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["worker_available"] is False
        assert payload["reason"] is not None
    _reset()


def test_list_pagination_and_filters(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            make_completed_report(db, ticker="600519.SH")
            make_completed_report(db, ticker="300059.SZ")
            job = UziReportJob(
                ticker_input="000001.SZ",
                status="failed",
                phase="failed",
                progress=10,
                error_code="UZI_STAGE1_FAILED",
                report_rel_dir="",
            )
            db.add(job)
            db.flush()
            job.report_rel_dir = str(job.id)

        # 分页。
        page = client.get(
            "/api/aniu/uzi/reports?limit=2&offset=0", headers=headers
        ).json()
        assert len(page["items"]) == 2
        assert page["total"] == 3
        assert page["limit"] == 2

        # 股票过滤（同时匹配标准代码/原始输入/公司名）。
        filtered = client.get(
            "/api/aniu/uzi/reports?ticker=600519", headers=headers
        ).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["ticker_normalized"] == "600519.SH"

        # 状态过滤。
        status = client.get(
            "/api/aniu/uzi/reports?status=failed", headers=headers
        ).json()
        assert status["total"] == 1
        assert status["items"][0]["status"] == "failed"

        # 非法状态。
        bad = client.get(
            "/api/aniu/uzi/reports?status=bogus", headers=headers
        )
        assert bad.status_code == 400
    _reset()


def test_list_item_has_no_full_sections(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            make_completed_report(db, ticker="600519.SH")
        page = client.get("/api/aniu/uzi/reports", headers=headers).json()
        item = page["items"][0]
        assert item["overall_score"] == 78.5
        assert item["verdict"] == "谨慎看多"
        assert "summary" not in item
        assert "risks" not in item
    _reset()


def test_artifact_whitelist_and_path_traversal(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            job = make_completed_report(db, ticker="600519.SH")
            report_id = job.id
            report_dir = uzi_report_service._resolve_report_dir(job.report_rel_dir)
            artifacts = report_dir / "artifacts"
            artifacts.mkdir(parents=True, exist_ok=True)
            (artifacts / "full-report-standalone.html").write_text("A" * 12000, encoding="utf-8")
            (artifacts / "synthesis.json").write_text('{"overall_score": 78.5, "verdict_label": "谨慎看多"}', encoding="utf-8")

        # 白名单 key 可用。
        html = client.get(f"/api/aniu/uzi/reports/{report_id}/artifacts/html", headers=headers)
        assert html.status_code == 200
        assert html.headers.get("x-content-type-options") == "nosniff"
        assert "text/html" in html.headers.get("content-type", "")
        assert len(html.content) == 12000

        syn = client.get(f"/api/aniu/uzi/reports/{report_id}/artifacts/synthesis", headers=headers)
        assert syn.status_code == 200
        assert "json" in syn.headers.get("content-type", "")

        # 未知 key 404。
        unknown = client.get(f"/api/aniu/uzi/reports/{report_id}/artifacts/../../etc/passwd", headers=headers)
        assert unknown.status_code == 404

        # 非白名单 key。
        bad_key = client.get(f"/api/aniu/uzi/reports/{report_id}/artifacts/evil", headers=headers)
        assert bad_key.status_code == 404
    _reset()


def test_artifact_requires_completed(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            job = UziReportJob(
                ticker_input="600519.SH",
                status="stage2_running",
                phase="stage2_running",
                progress=85,
                report_rel_dir="",
            )
            db.add(job)
            db.flush()
            job.report_rel_dir = str(job.id)
            report_id = job.id
        response = client.get(
            f"/api/aniu/uzi/reports/{report_id}/artifacts/html", headers=headers
        )
        assert response.status_code == 409
    _reset()


def test_sse_snapshot_terminal_closes(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            job = make_completed_report(db, ticker="600519.SH")
            report_id = job.id
        with client.stream(
            "GET", f"/api/aniu/uzi/reports/{report_id}/events", headers=headers
        ) as response:
            assert response.status_code == 200
            body = ""
            for line in response.iter_lines():
                if line:
                    body += line + "\n"
            assert "event: snapshot" in body
            assert "event: completed" in body
    _reset()


def test_sse_snapshot_missing_report(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with client.stream(
            "GET", "/api/aniu/uzi/reports/99999/events", headers=headers
        ) as response:
            assert response.status_code == 200
            body = "".join(line + "\n" for line in response.iter_lines() if line)
            assert "event: snapshot" in body
            assert '"job": null' in body
    _reset()


def test_detail_cleans_artifacts(monkeypatch, tmp_path) -> None:
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            job = make_completed_report(db, ticker="600519.SH")
            report_id = job.id
        detail = client.get(f"/api/aniu/uzi/reports/{report_id}", headers=headers)
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["status"] == "completed"
        assert payload["summary"]["overall_score"] == 78.5
        keys = [item["key"] for item in payload["artifacts"]]
        assert "html" in keys and "synthesis" in keys
        # 不泄露绝对路径。
        assert "report_rel_dir" not in payload
        assert "/" not in str(payload["artifacts"][0]["file"])
    _reset()


def _reset() -> None:
    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()
    rate_limit_module._limiter.reset()
    uzi_report_service._cancel_events.clear()
    uzi_report_service.reset_rate_limit()
