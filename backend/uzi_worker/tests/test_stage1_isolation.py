"""Stage 1 目录隔离测试（§20.2：独立目录、不共享 cache、report_rel_dir 校验）。"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _poll_until(predicate, timeout: float = 10.0, interval: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _job(client, report_id: str, headers) -> dict:
    return client.get(f"/internal/jobs/{report_id}", headers=headers).json()["job"]


def test_stage1_creates_isolated_directories(worker_client, worker_env, auth_headers):
    report_root = worker_env["report_root"]

    r1 = worker_client.post(
        "/internal/jobs/101/stage1",
        json={"ticker": "600519.SH", "report_rel_dir": "101"},
        headers=auth_headers,
    )
    assert r1.status_code == 202
    r2 = worker_client.post(
        "/internal/jobs/102/stage1",
        json={"ticker": "000001.SZ", "report_rel_dir": "102"},
        headers=auth_headers,
    )
    assert r2.status_code == 202

    # 两个任务的工作目录与 .cache 必须隔离（§12.3）。
    cache_101 = report_root / "101" / "work" / ".cache"
    cache_102 = report_root / "102" / "work" / ".cache"
    assert cache_101 != cache_102
    assert cache_101.parent != cache_102.parent

    # 等待两个任务各自完成 Stage 1。
    assert _poll_until(
        lambda: _job(worker_client, "101", auth_headers)["status"] == "succeeded"
    )
    assert _poll_until(
        lambda: _job(worker_client, "102", auth_headers)["status"] == "succeeded"
    )

    manifest_101 = json.loads(
        (report_root / "101" / "work" / "stage1-manifest.json").read_text(encoding="utf-8")
    )
    manifest_102 = json.loads(
        (report_root / "102" / "work" / "stage1-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest_101["ticker_normalized"] == "600519.SH"
    assert manifest_102["ticker_normalized"] == "000001.SZ"
    assert set(manifest_101["files"]) >= {"raw_data.json", "dimensions.json", "panel.json"}


def test_stage1_rejects_rel_dir_mismatch(worker_client, auth_headers):
    response = worker_client.post(
        "/internal/jobs/201/stage1",
        json={"ticker": "600519.SH", "report_rel_dir": "202"},
        headers=auth_headers,
    )
    assert response.status_code == 400
    assert "report_rel_dir" in response.json()["detail"]


def test_stage1_rejects_duplicate_active_job(worker_client, auth_headers):
    first = worker_client.post(
        "/internal/jobs/301/stage1",
        json={"ticker": "600519.SH", "report_rel_dir": "301"},
        headers=auth_headers,
    )
    assert first.status_code == 202
    second = worker_client.post(
        "/internal/jobs/301/stage1",
        json={"ticker": "600519.SH", "report_rel_dir": "301"},
        headers=auth_headers,
    )
    assert second.status_code == 409


def test_stage1_rejects_bad_ticker(worker_client, auth_headers):
    for bad, expected_status in [
        ("", 422),
        ("a" * 65, 422),
        ("600519/../etc", 422),
        ("半;行", 422),
        ("`id`", 422),
    ]:
        response = worker_client.post(
            "/internal/jobs/401/stage1",
            json={"ticker": bad, "report_rel_dir": "401"},
            headers=auth_headers,
        )
        assert response.status_code == expected_status, bad


def test_stage1_unresolved_ticker_maps_to_stable_code(
    worker_client, worker_env, auth_headers, monkeypatch
):
    """中文名解析失败 → 结构化错误码（§20.2）。"""
    monkeypatch.setenv("UZI_MOCK_FAIL_RESOLVE", "1")
    monkeypatch.setenv("UZI_MOCK_SLEEP_SECONDS", "0")
    response = worker_client.post(
        "/internal/jobs/501/stage1",
        json={"ticker": "不存在的公司名", "report_rel_dir": "501"},
        headers=auth_headers,
    )
    assert response.status_code == 202
    assert _poll_until(
        lambda: _job(worker_client, "501", auth_headers)["status"] == "failed"
    )
    job = _job(worker_client, "501", auth_headers)
    assert job["error_code"] == "UZI_UNRESOLVED_TICKER"


def test_stage1_non_stock_stops_early(worker_client, auth_headers, monkeypatch):
    """ETF/指数等非个股标的 → 结构化失败，禁止继续生成空报告（§5.2 第 5 条）。"""
    monkeypatch.setenv("UZI_MOCK_NON_STOCK", "1")
    response = worker_client.post(
        "/internal/jobs/502/stage1",
        json={"ticker": "510300", "report_rel_dir": "502"},
        headers=auth_headers,
    )
    assert response.status_code == 202
    assert _poll_until(
        lambda: _job(worker_client, "502", auth_headers)["status"] == "failed"
    )
    job = _job(worker_client, "502", auth_headers)
    assert job["error_code"] == "UZI_NON_STOCK_SECURITY"


def test_stage1_response_hides_report_root(
    worker_client, worker_env, auth_headers
):
    """API 返回不得泄露宿主机绝对路径。"""
    response = worker_client.post(
        "/internal/jobs/601/stage1",
        json={"ticker": "600519.SH", "report_rel_dir": "601"},
        headers=auth_headers,
    )
    assert response.status_code == 202
    body_text = response.text
    assert str(worker_env["report_root"]) not in body_text
    assert "/app/data" not in body_text