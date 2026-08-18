"""回归测试：取消 → 删除 → 同一标的重新分析，新任务不得立即变成 cancelled。

issue 场景：
1. 任务卡住 → 用户点取消：`cancel_report` 把 `_cancel_events[report_id]` 闩锁置位，
   且该闩锁只在任务存活期有意义，但从不清理。
2. 用户删除任务：DB 行与报告目录被删除，但闩锁仍留在内存字典里。
3. 用户重新提交同一标的：SQLite 行号表（无 AUTOINCREMENT）会复用刚删除的
   最大行号 → 新任务拿到相同 report_id → `_raise_if_cancelled` 命中旧闩锁 →
   一提交就被判为取消。

修复：创建/删除任务时清除该 id 的陈旧闩锁与事件总线通道；执行循环用
“运行代际”（created_at）识别同 id 的新任务，旧执行尸体不得污染新任务。
"""
from __future__ import annotations

from pathlib import Path
import sys
import time

sys.path.append(str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.db.database import session_scope
from app.db.models import UziReportJob
from app.services.uzi_event_bus import uzi_event_bus
from app.services.uzi_report_service import uzi_report_service
from tests.uzi_test_helpers import auth_headers, create_uzi_test_client


def _wait_terminal(report_id: int, timeout: float = 15.0) -> UziReportJob | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            if job is not None and job.status in {"completed", "failed", "cancelled"}:
                return job
        time.sleep(0.1)
    with session_scope() as db:
        return db.get(UziReportJob, report_id)


def test_cancel_delete_recreate_same_ticker_not_instantly_cancelled(
    monkeypatch, tmp_path
) -> None:
    """用户场景复现：取消 → 删除 → 同标的重新分析，必须正常执行而非立即取消。"""
    client, _worker = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)

        # 1. 构造一个“运行中/卡住”的任务（手动插入，不进执行器队列，避免竞态）。
        with session_scope() as db:
            job_a = UziReportJob(
                ticker_input="600519.SH",
                status="stage1_running",
                phase="stage1_running",
                progress=30,
                progress_message="数据采集中。",
                uzi_commit="7bc779d",
                report_rel_dir="",
            )
            db.add(job_a)
            db.flush()
            job_a.report_rel_dir = str(job_a.id)
            db.commit()
            old_id = job_a.id

        # 2. 取消 → 任务变 cancelled，且取消闩锁被置位（任务存活期内的正常行为）。
        response = client.post(f"/api/aniu/uzi/reports/{old_id}/cancel", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["cancelled"] is True
        with uzi_report_service._cancel_lock:
            latch = uzi_report_service._cancel_events.get(old_id)
        assert latch is not None and latch.is_set(), "取消闩锁应被置位"

        # 3. 删除任务。
        response = client.delete(f"/api/aniu/uzi/reports/{old_id}", headers=headers)
        assert response.status_code == 204, response.text
        with session_scope() as db:
            assert db.get(UziReportJob, old_id) is None
        # 修复点：删除后闩锁必须被清除（否则同 id 新任务会被旧闩锁误伤）。
        with uzi_report_service._cancel_lock:
            latch_after_delete = uzi_report_service._cancel_events.get(old_id)
        assert latch_after_delete is None, "删除任务后必须清除取消闩锁"

        # 4. 重新提交同一标的。
        # SQLite 行号复用：新任务会拿到与刚删除任务相同的 id（复现场景的前提）。
        response = client.post(
            "/api/aniu/uzi/reports", json={"ticker": "600519.SH"}, headers=headers
        )
        assert response.status_code == 202, response.text
        payload = response.json()
        assert payload["reused"] is False
        new_id = payload["report"]["id"]
        assert new_id == old_id, "SQLite 应复用刚删除任务的行号（复现场景）"

        # 修复点：新任务创建后，同 id 不得残留旧闩锁。
        with uzi_report_service._cancel_lock:
            assert uzi_report_service._cancel_events.get(new_id) is None

        # 5. 关键断言：新任务必须正常走到终态，而不是一提交就 cancelled。
        job_b = _wait_terminal(new_id)
        assert job_b is not None
        assert job_b.status == "completed", (
            f"重新分析被旧取消状态污染: status={job_b.status} "
            f"error={job_b.error_message}"
        )


def test_delete_clears_cancel_latch(monkeypatch, tmp_path) -> None:
    """删除任务后，该 id 的取消闩锁与事件通道都必须清理（防止 id 复用误伤）。"""
    client, _worker = create_uzi_test_client(monkeypatch, tmp_path)
    with client:
        headers = auth_headers(client)
        with session_scope() as db:
            job = UziReportJob(
                ticker_input="600000.SH",
                status="llm_review",
                phase="llm_review",
                progress=50,
                uzi_commit="7bc779d",
                report_rel_dir="",
            )
            db.add(job)
            db.flush()
            job.report_rel_dir = str(job.id)
            db.commit()
            report_id = job.id

        client.post(f"/api/aniu/uzi/reports/{report_id}/cancel", headers=headers)
        # 事件通道存在旧终态事件（模拟 120s 回放窗口内的旧 cancelled）。
        uzi_event_bus.publish(report_id, "cancelled", {"message": "旧任务取消"})

        response = client.delete(f"/api/aniu/uzi/reports/{report_id}", headers=headers)
        assert response.status_code == 204, response.text

        with uzi_report_service._cancel_lock:
            assert uzi_report_service._cancel_events.get(report_id) is None
        assert uzi_event_bus._get(report_id) is None, "删除后事件通道应被丢弃"