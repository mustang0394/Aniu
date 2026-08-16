"""UZI 深度报告模块公共 API Schema（文档 §10）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# 任务状态机（文档 §6）
UZI_REPORT_STATUSES: tuple[str, ...] = (
    "queued",
    "stage1_running",
    "llm_review",
    "stage2_running",
    "completed",
    "failed",
    "cancelled",
)

UZI_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "cancelled"}
)

# 产物白名单（文档 §10.8）：key 只能来自清单映射，不接受文件名/相对路径。
UZI_ARTIFACT_KEYS: tuple[str, ...] = (
    "html",
    "share_card",
    "war_report",
    "meta",
    "one_liner",
    "synthesis",
)

UziReportStatus = Literal[
    "queued",
    "stage1_running",
    "llm_review",
    "stage2_running",
    "completed",
    "failed",
    "cancelled",
]


class UziReportCreateRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=64)

    @field_validator("ticker")
    @classmethod
    def _strip_ticker(cls, value: str) -> str:
        return value.strip()


class UziReportSummaryRead(BaseModel):
    """历史列表项：不含完整章节（文档 §10.3）。"""

    id: int
    ticker_input: str
    ticker_normalized: str | None = None
    company_name: str | None = None
    status: UziReportStatus
    progress: int = 0
    overall_score: float | None = None
    verdict: str | None = None
    llm_model: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
    data_as_of: datetime | None = None


class UziArtifactRead(BaseModel):
    """清洗后的产物清单条目：只暴露相对文件名、大小、MIME 与 key。"""

    key: str
    file: str
    size: int
    mime: str


class UziReportDetailRead(UziReportSummaryRead):
    """报告详情：完整状态 + summary + 清洗后的产物清单（§10.4）。"""

    phase: str | None = None
    progress_message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    uzi_commit: str | None = None
    llm_reasoning_effort: str | None = None
    summary: dict[str, Any] | None = None
    artifacts: list[UziArtifactRead] = Field(default_factory=list)


class UziReportListResponse(BaseModel):
    items: list[UziReportSummaryRead] = Field(default_factory=list)
    total: int = 0
    limit: int = 20
    offset: int = 0


class UziStatusRead(BaseModel):
    """模块状态（文档 §10.1）。"""

    enabled: bool
    worker_available: bool
    worker_version: str | None = None
    active_jobs: int = 0
    queued_jobs: int = 0
    max_queued: int = 3
    reason: str | None = None


class UziReportCreateResponse(BaseModel):
    report: UziReportSummaryRead
    reused: bool = False


class UziCancelResponse(BaseModel):
    id: int
    status: UziReportStatus
    cancelled: bool = False
