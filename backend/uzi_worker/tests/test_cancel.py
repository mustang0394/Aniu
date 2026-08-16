"""取消测试（§20.2：只终止目标子进程，SIGTERM→等待→SIGKILL）。"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.models import WorkerJobState
from app.state_store import StateStore

_MOCK_SLEEP = "30"


@pytest.fixture()
def slow_job_env(worker_env, monkeypatch):
    monkeypatch.setenv("UZI_MOCK_SLEEP_SECONDS", _MOCK_SLEEP)
    return worker_env


def _start_unrelated_sleeper(tmp_path: Path) -> subprocess.Popen:
    """与目标任务无关的独立进程，用于验证取消不会波及。"""
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def test_cancel_terminates_only_target_process(
    slow_job_env, worker_client, auth_headers, tmp_path
):
    report_root = Path(slow_job_env["report_root"])

    # 无关进程先启动。
    unrelated = _start_unrelated_sleeper(tmp_path)
    unrelated_pid = unrelated.pid

    response = worker_client.post(
        "/internal/jobs/901/stage1",
        json={"ticker": "600519.SH", "report_rel_dir": "901"},
        headers=auth_headers,
    )
    assert response.status_code == 202
    job = worker_client.get("/internal/jobs/901", headers=auth_headers).json()["job"]
    target_pid = job["worker_pid"] if "worker_pid" in job else None
    assert target_pid is not None

    try:
        cancel = worker_client.post("/internal/jobs/901/cancel", headers=auth_headers)
        assert cancel.status_code == 200
        cancelled = cancel.json()["job"]
        assert cancelled["status"] == "cancelled"
        assert cancelled["error_code"] == "UZI_CANCELLED"

        # 目标进程组应很快消失。
        deadline = time.monotonic() + 15
        target_gone = False
        while time.monotonic() < deadline:
            try:
                os.killpg(target_pid, 0)
            except (ProcessLookupError, PermissionError):
                target_gone = True
                break
            time.sleep(0.3)
        assert target_gone, "目标进程组未在宽限期内终止"

        # 无关进程仍然存活。
        assert unrelated.poll() is None, "无关进程不应被取消波及"

        # 取消是幂等操作（§20.1：取消终态任务是幂等操作）。
        again = worker_client.post("/internal/jobs/901/cancel", headers=auth_headers)
        assert again.status_code == 200
        assert again.json()["job"]["status"] == "cancelled"
    finally:
        if unrelated.poll() is None:
            try:
                os.killpg(unrelated.pid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass


def test_cancel_unknown_job_404(worker_client, auth_headers):
    response = worker_client.post("/internal/jobs/99999/cancel", headers=auth_headers)
    assert response.status_code == 404


def test_state_file_persists_cancelled(slow_job_env, worker_client, auth_headers):
    report_root = Path(slow_job_env["report_root"])
    response = worker_client.post(
        "/internal/jobs/902/stage1",
        json={"ticker": "600519.SH", "report_rel_dir": "902"},
        headers=auth_headers,
    )
    assert response.status_code == 202
    worker_client.post("/internal/jobs/902/cancel", headers=auth_headers)

    # worker-state.json 是状态真相（§17.2）。
    state_path = report_root / "902" / "worker-state.json"
    assert state_path.is_file()
    store = StateStore(report_root)
    state: WorkerJobState | None = store.get("902")
    assert state is not None
    assert state.status == "cancelled"
    assert state.is_terminal