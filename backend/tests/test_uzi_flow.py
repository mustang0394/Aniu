"""UZI 完整状态流转集成测试（假 Worker，文档 §20.1）。"""

from __future__ import annotations

from pathlib import Path
import sys
import time

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


def test_full_flow_reaches_completed(monkeypatch, tmp_path) -> None:
    """假 Worker + mock LLM 评审：queued → completed 完整状态流转。"""
    client, worker = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        response = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "600519.SH"},
            headers=headers,
        )
        assert response.status_code == 202
        report_id = response.json()["report"]["id"]

        # 执行器为后台线程，等待任务终态。
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with session_scope() as db:
                job = db.get(UziReportJob, report_id)
                if job is not None and job.status in {
                    "completed", "failed", "cancelled"
                }:
                    break
            time.sleep(0.1)
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            assert job.status == "completed", f"实际状态: {job.status} {job.error_message}"
            assert job.progress == 100
            assert job.ticker_normalized == "600519.SH"
            assert job.summary_json is not None
            assert job.summary_json.get("verdict") == "谨慎看多"
            assert job.artifact_manifest_json is not None
            # P1回归（review问题2）：归一化后详情摘要字段非空
            sj = job.summary_json
            assert sj.get("valuation", {}).get("rating") == "买入", "估值评级应来自 institutional_modeling.initiating_rating"
            assert sj.get("valuation", {}).get("target_price") == 1850.0
            assert sj.get("catalysts") == ["Q2 业绩预告", "分红派息", "新品发布"], "催化剂应来自 dashboard.intelligence.catalysts"
            assert sj.get("panel", {}).get("bullish") == 21, "投资者统计应来自 panel.json.signal_distribution"
            assert sj.get("panel", {}).get("bearish") == 12
            assert sj.get("data_gaps", {}).get("coverage_pct") == 92.0, "数据缺口应来自 synthesis.data_gaps"
            assert sj.get("data_gaps", {}).get("unresolved") == 1
            assert sj.get("data_as_of") == "2026-08-16T00:00:00"
            assert "贵州茅台" in sj.get("one_liner", ""), "one_liner 应来自 artifacts/one-liner.txt"
    _reset()


def test_stage1_failure_marks_failed(monkeypatch, tmp_path) -> None:
    worker = FakeWorkerClient()
    worker.stage1_fail_code = "UZI_NON_STOCK_SECURITY"
    client, _ = create_uzi_test_client(monkeypatch, tmp_path, worker=worker)
    with client:
        headers = auth_headers(client)
        response = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "510300.SH"},
            headers=headers,
        )
        report_id = response.json()["report"]["id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with session_scope() as db:
                job = db.get(UziReportJob, report_id)
                if job is not None and job.status in {
                    "completed", "failed", "cancelled"
                }:
                    break
            time.sleep(0.1)
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            assert job.status == "failed"
            assert job.error_code == "UZI_NON_STOCK_SECURITY"
    _reset()


def test_create_rate_limited(monkeypatch, tmp_path) -> None:
    """同一登录来源创建任务的最小间隔限流（§8 UZI_CREATE_RATE_LIMIT_SECONDS）。"""
    client, _ = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        first = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "600519.SH"},
            headers=headers,
        )
        assert first.status_code == 202
        second = client.post(
            "/api/aniu/uzi/reports",
            json={"ticker": "300059.SZ"},
            headers=headers,
        )
        assert second.status_code == 429
    _reset()


def _reset() -> None:
    database_module._engine = None
    database_module._session_local = None
    get_settings.cache_clear()
    rate_limit_module._limiter.reset()
    uzi_report_service._cancel_events.clear()
    uzi_report_service.reset_rate_limit()
