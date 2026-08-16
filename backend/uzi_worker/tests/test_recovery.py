"""Stage1 源码缺失 / Worker 重启恢复测试（§17.2 / §20.2）。"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from app.models import WorkerJobState
from app.state_store import StateStore


def test_stage1_source_missing_returns_503(worker_env, monkeypatch):
    """非 mock 模式且 UZI 源码不存在时返回 503（UZI 源码未安装）。"""
    monkeypatch.setenv("UZI_WORKER_MOCK", "0")
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as client:
        response = client.post(
            "/internal/jobs/11/stage1",
            json={"ticker": "600519.SH", "report_rel_dir": "11"},
            headers={"X-Aniu-Uzi-Token": "test-worker-token-123"},
        )
        assert response.status_code in {503, 500}


def test_restart_marks_interrupted_jobs_failed(worker_env, tmp_path):
    """Worker 重启：非终态任务标记 failed(UZI_INTERRUPTED)，不自动重跑（§17.2）。"""
    report_root = Path(worker_env["report_root"])
    report_dir = report_root / "10"
    report_dir.mkdir(parents=True)

    # 模拟一个旧的非终态任务：running + 不存在的 pid。
    old_state = WorkerJobState(report_id="10")
    old_state.mark_running(
        phase="stage1_running", progress=10, message="旧任务中断前状态"
    )
    old_state.worker_pid = 99999999  # 肯定不存在
    (report_dir / "worker-state.json").write_text(
        json.dumps(old_state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    store = StateStore(report_root)
    recovered = store.get("10")
    assert recovered is not None
    assert recovered.status == "failed"
    assert recovered.error_code == "UZI_INTERRUPTED"

    # 终态任务不应被改写。
    done_dir = report_root / "11"
    done_dir.mkdir(parents=True)
    done_state = WorkerJobState(report_id="11")
    done_state.mark_succeeded(phase="completed")
    (done_dir / "worker-state.json").write_text(
        json.dumps(done_state.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    store2 = StateStore(report_root)
    assert store2.get("11").status == "succeeded"