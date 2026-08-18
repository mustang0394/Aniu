"""Worker 状态持久化：内存 + worker-state.json 双写（文档 §17.2）。

- 每个报告目录下有一份 ``worker-state.json``。
- Stage 子进程与主进程都会原子更新该文件（tmp 文件 + os.replace）。
- Worker 重启时扫描所有状态文件，把没有执行进程的非终态任务
  标记为 ``failed``（错误码 ``UZI_INTERRUPTED``），绝不自动重跑采集。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from app.models import (
    TERMINAL_STATUSES,
    ERROR_INTERRUPTED,
    WorkerJobState,
)

logger = logging.getLogger(__name__)

_STATE_FILENAME = "worker-state.json"


class StateStore:
    def __init__(self, report_root: Path, *, recover: bool = True) -> None:
        self._report_root = Path(report_root)
        self._lock = threading.RLock()
        self._cache: dict[str, WorkerJobState] = {}
        self._report_root.mkdir(parents=True, exist_ok=True)
        if recover:
            self._recover_interrupted()

    # ── 路径 ──────────────────────────────────────────────
    def report_dir(self, report_id: str) -> Path:
        return self._report_root / report_id

    def state_path(self, report_id: str) -> Path:
        return self.report_dir(report_id) / _STATE_FILENAME

    # ── 读写 ──────────────────────────────────────────────
    def get(self, report_id: str) -> WorkerJobState | None:
        with self._lock:
            path = self.state_path(report_id)
            if path.is_file():
                state = self._read_file(path)
                if state is not None:
                    self._cache[report_id] = state
                    return state
            # 磁盘状态文件已不存在（报告被删除或目录被清理）：
            # 必须丢弃内存缓存，否则会向调用方返回已删除任务的过期终态，
            # 导致同一 report_id 重新提交时被直接判定为 failed/cancelled
            # 而不会真正重新执行（issue: 删除失败报告后重建立即报同样错）。
            self._cache.pop(report_id, None)
            return None

    def upsert(self, state: WorkerJobState) -> WorkerJobState:
        with self._lock:
            self._cache[state.report_id] = state
            self._write_file(self.state_path(state.report_id), state.to_dict())
            return state

    def update(self, report_id: str, *, apply) -> WorkerJobState | None:
        """读-改-写循环：用于子进程与主进程并发更新同一任务状态。"""
        with self._lock:
            state = self.get(report_id)
            if state is None:
                return None
            apply(state)
            self.upsert(state)
            return state

    # ── 重启恢复（§17.2）──────────────────────────────────
    def _recover_interrupted(self) -> None:
        if not self._report_root.is_dir():
            return
        recovered = 0
        for child in sorted(self._report_root.iterdir()):
            if not child.is_dir():
                continue
            path = child / _STATE_FILENAME
            if not path.is_file():
                continue
            state = self._read_file(path)
            if state is None or state.is_terminal:
                continue
            if state.worker_pid is not None and self._pid_alive(state.worker_pid):
                # 进程仍存活：Worker 重启后无法可靠接管其监控与清理，
                # 安全起见标记失败，由主服务对账（§17.1）决定是否从清单恢复。
                state.mark_failed(
                    error_code=ERROR_INTERRUPTED,
                    error_message="Worker 重启导致任务中断，请重新生成报告。",
                )
            else:
                state.mark_failed(
                    error_code=ERROR_INTERRUPTED,
                    error_message="Worker 重启导致任务中断，请重新生成报告。",
                )
            self._write_file(path, state.to_dict())
            self._cache[state.report_id] = state
            recovered += 1
        if recovered:
            logger.warning("重启恢复：已将 %d 个非终态任务标记为 failed", recovered)

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    # ── 文件 IO ───────────────────────────────────────────
    def _read_file(self, path: Path) -> WorkerJobState | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        return WorkerJobState.from_dict(payload)

    def _write_file(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, path)

    def active_jobs(self) -> int:
        return len(
            [
                state
                for state in self.all_states()
                if state.status in {"accepted", "running"}
            ]
        )

    def all_states(self) -> list[WorkerJobState]:
        with self._lock:
            states: dict[str, WorkerJobState] = dict(self._cache)
            if self._report_root.is_dir():
                for child in sorted(self._report_root.iterdir()):
                    if not child.is_dir():
                        continue
                    path = child / _STATE_FILENAME
                    if path.is_file():
                        state = self._read_file(path)
                        if state is not None:
                            states[state.report_id] = state
            return list(states.values())


def is_terminal_status(status: str) -> bool:
    return status in TERMINAL_STATUSES