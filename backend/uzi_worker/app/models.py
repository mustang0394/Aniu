"""Worker 内部任务状态数据结构与错误码。

状态枚举遵循文档 §11.3（accepted/running/succeeded/failed/cancelled），
错误码集合见文档 §17.3（Worker 侧使用其中与运行阶段相关的部分）。
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class WorkerStatus(str, Enum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        WorkerStatus.SUCCEEDED.value,
        WorkerStatus.FAILED.value,
        WorkerStatus.CANCELLED.value,
    }
)

# 稳定错误码（文档 §17.3 子集，Worker 侧实际可能产生）。
ERROR_UNRESOLVED_TICKER = "UZI_UNRESOLVED_TICKER"
ERROR_NON_STOCK_SECURITY = "UZI_NON_STOCK_SECURITY"
ERROR_STAGE1_FAILED = "UZI_STAGE1_FAILED"
ERROR_STAGE2_FAILED = "UZI_STAGE2_FAILED"
ERROR_ARTIFACT_INVALID = "UZI_ARTIFACT_INVALID"
ERROR_JOB_TIMEOUT = "UZI_JOB_TIMEOUT"
ERROR_INTERRUPTED = "UZI_INTERRUPTED"
ERROR_SOURCE_MISSING = "UZI_SOURCE_MISSING"

# 任务目录内相对路径约定（文档 §12.2），对外只暴露相对路径。
STAGE1_MANIFEST_REL = "work/stage1-manifest.json"
AGENT_ANALYSIS_REL = "work/agent_analysis.json"
ARTIFACTS_REL = "artifacts"
ARTIFACTS_TMP_REL = "artifacts.tmp"

# Stage 2 产物文件名（文档 §12.2 契约，与主服务 _ARTIFACT_KEY_FILES 一致）。
STAGE2_ARTIFACT_FILES = (
    "full-report-standalone.html",
    "report.meta.json",
    "one-liner.txt",
    "synthesis.json",
    "share-card.png",
    "war-report.png",
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class WorkerJobState:
    """单个 UZI 报告任务的 Worker 侧状态。

    ``worker_pid`` 是该任务 Stage 进程的进程组首进程 PID（POSIX
    ``start_new_session`` 创建），取消时对整个进程组发信号。
    """

    report_id: str
    status: str = WorkerStatus.ACCEPTED.value
    phase: str = "accepted"
    progress: int = 0
    progress_message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    worker_pid: int | None = None
    stage1_manifest_rel: str = STAGE1_MANIFEST_REL
    agent_analysis_rel: str = AGENT_ANALYSIS_REL
    artifacts_rel: str = ARTIFACTS_REL
    started_at: str | None = None
    finished_at: str | None = None
    updated_at: str = field(default_factory=_utc_now_iso)

    def mark_running(self, *, phase: str, progress: int, message: str) -> None:
        self.status = WorkerStatus.RUNNING.value
        self.phase = phase
        self.progress = progress
        self.progress_message = message
        self.updated_at = _utc_now_iso()

    def mark_succeeded(self, *, phase: str, progress: int = 100,
                       message: str | None = None) -> None:
        self.status = WorkerStatus.SUCCEEDED.value
        self.phase = phase
        self.progress = progress
        self.progress_message = message if message is not None else self.progress_message
        self.finished_at = _utc_now_iso()
        self.updated_at = _utc_now_iso()

    def mark_failed(self, *, error_code: str, error_message: str,
                    phase: str | None = None) -> None:
        self.status = WorkerStatus.FAILED.value
        self.phase = phase or self.phase
        self.error_code = error_code
        self.error_message = error_message
        self.finished_at = _utc_now_iso()
        self.updated_at = _utc_now_iso()

    def mark_cancelled(self, *, message: str = "任务已被取消。") -> None:
        self.status = WorkerStatus.CANCELLED.value
        self.error_code = "UZI_CANCELLED"
        self.error_message = message
        self.finished_at = _utc_now_iso()
        self.updated_at = _utc_now_iso()

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkerJobState":
        known = {
            key: value
            for key, value in payload.items()
            if key in cls.__dataclass_fields__  # type: ignore[attr-defined]
        }
        return cls(**known)

    def to_api_dict(self) -> dict[str, Any]:
        """对外返回的脱敏视图（不含绝对路径，只含相对路径与阶段信息）。

        内部接口（携带共享密钥）需要 worker_pid 以便主服务侧排查/联动。
        """
        return {
            "report_id": self.report_id,
            "status": self.status,
            "phase": self.phase,
            "progress": self.progress,
            "progress_message": self.progress_message,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "worker_pid": self.worker_pid,
            "stage1_manifest_rel": self.stage1_manifest_rel,
            "agent_analysis_rel": self.agent_analysis_rel,
            "artifacts_rel": self.artifacts_rel,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
        }


def monotonic_now() -> float:
    return time.monotonic()