"""Token 认证测试（§20.2：Token 校验）。"""
from __future__ import annotations


def test_health_exempts_token(worker_client):
    response = worker_client.get("/internal/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["mock"] is True
    assert body["token_configured"] is True


def test_stage1_rejects_missing_token(worker_client):
    response = worker_client.post(
        "/internal/jobs/1/stage1",
        json={"ticker": "600519.SH", "report_rel_dir": "1"},
    )
    assert response.status_code == 401


def test_stage1_rejects_wrong_token(worker_client):
    response = worker_client.post(
        "/internal/jobs/1/stage1",
        json={"ticker": "600519.SH", "report_rel_dir": "1"},
        headers={"X-Aniu-Uzi-Token": "wrong-token"},
    )
    assert response.status_code == 401


def test_get_job_rejects_wrong_token(worker_client):
    response = worker_client.get(
        "/internal/jobs/1", headers={"X-Aniu-Uzi-Token": "wrong-token"}
    )
    assert response.status_code == 401


def test_cancel_rejects_wrong_token(worker_client):
    response = worker_client.post(
        "/internal/jobs/1/cancel", headers={"X-Aniu-Uzi-Token": "wrong-token"}
    )
    assert response.status_code == 401


def test_stage2_rejects_wrong_token(worker_client):
    response = worker_client.post(
        "/internal/jobs/1/stage2",
        json={"ticker": "600519.SH"},
        headers={"X-Aniu-Uzi-Token": "wrong-token"},
    )
    assert response.status_code == 401


def test_source_update_endpoints_require_token(worker_client):
    assert worker_client.get("/internal/source/status").status_code == 401
    assert worker_client.post("/internal/source/update").status_code == 401


def test_no_token_configured_rejects_all(worker_env, monkeypatch, tmp_path):
    """未配置 UZI_WORKER_TOKEN 时所有受保护接口返回 401。"""
    monkeypatch.delenv("UZI_WORKER_TOKEN", raising=False)
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        health = client.get("/internal/health")
        assert health.status_code == 200
        assert health.json()["token_configured"] is False

        response = client.post(
            "/internal/jobs/1/stage1",
            json={"ticker": "600519.SH", "report_rel_dir": "1"},
            headers={"X-Aniu-Uzi-Token": "anything"},
        )
        assert response.status_code == 401
