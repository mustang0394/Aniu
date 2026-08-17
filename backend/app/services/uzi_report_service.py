"""UZI 深度报告任务编排服务（文档 §5 / §6 / §16 / §17.1 / §17.3）。

职责边界（§4）：

- 主服务是任务状态、数据库、鉴权、LLM 调用和历史报告的唯一业务所有者。
- Worker 是无业务数据库的执行节点：只接受受控任务、运行 UZI、
  写入共享目录并返回状态。
- 本服务持有单线程执行器（§8 ``UZI_MAX_ACTIVE=1``），串行推进任务状态机。

LLM 深度评审（§13）由批次3 实现：本批次在 ``run_llm_review`` 提供钩子，
默认行为是——若 LLM 配置完整且开启了 ``UZI_LLM_REVIEW_MOCK=1``（仅测试/
联调用）则写入最小合法 ``agent_analysis.json``；否则抛
``UZI_LLM_REVIEW_FAILED``，绝不写入空壳 ``agent_reviewed=true`` 冒充完整
报告（§5.3 禁止项）。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import re
import shutil
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import func, or_, select

from app.core.config import get_settings
from app.db.database import session_scope
from app.db.models import AppSettings, UziReportJob
from app.schemas.uzi import (
    UZI_ARTIFACT_KEYS,
    UZI_REPORT_STATUSES,
    UZI_TERMINAL_STATUSES,
    UziArtifactRead,
    UziReportDetailRead,
    UziReportSummaryRead,
)
from app.services.uzi_event_bus import uzi_event_bus
from app.services.uzi_llm_orchestrator import (
    UziLlmOrchestrator,
    UziReviewCancelled,
    UziReviewError,
)
from app.services.uzi_worker_client import (
    UZI_WORKER_UNAVAILABLE,
    UziWorkerClient,
    WorkerJobPayloadError,
    uzi_worker_client,
)

logger = logging.getLogger(__name__)

# 稳定错误码（文档 §17.3）
ERROR_DISABLED = "UZI_DISABLED"
ERROR_WORKER_UNAVAILABLE = "UZI_WORKER_UNAVAILABLE"
ERROR_QUEUE_FULL = "UZI_QUEUE_FULL"
ERROR_INVALID_TICKER = "UZI_INVALID_TICKER"
ERROR_LLM_NOT_CONFIGURED = "UZI_LLM_NOT_CONFIGURED"
ERROR_LLM_REVIEW_FAILED = "UZI_LLM_REVIEW_FAILED"
ERROR_STAGE1_FAILED = "UZI_STAGE1_FAILED"
ERROR_STAGE2_FAILED = "UZI_STAGE2_FAILED"
ERROR_ARTIFACT_INVALID = "UZI_ARTIFACT_INVALID"
ERROR_JOB_TIMEOUT = "UZI_JOB_TIMEOUT"
ERROR_ORPHANED_JOB = "UZI_ORPHANED_JOB"
ERROR_CANCELLED = "UZI_CANCELLED"
ERROR_UPDATE_BUSY = "UZI_UPDATE_BUSY"
ERROR_UPDATE_FAILED = "UZI_UPDATE_FAILED"

# 输入校验（与 Worker 侧 _TICKER_INVALID_RE 一致）：拒绝控制字符、
# 路径分隔符与明显命令字符。用户输入只作为函数参数传递，绝不拼接 Shell。
_TICKER_INVALID_RE = re.compile(r"[\x00-\x1f\x7f/\\;`$|<>&]")
_MAX_TICKER_LENGTH = 64

# 产物逻辑 key → artifacts/ 内文件名（文档 §12.2，与上游 stage2 产物名一致）。
_ARTIFACT_KEY_FILES: dict[str, str] = {
    "html": "full-report-standalone.html",
    "share_card": "share-card.png",
    "war_report": "war-report.png",
    "meta": "report.meta.json",
    "one_liner": "one-liner.txt",
    "synthesis": "synthesis.json",
}

_MIME_BY_EXTENSION: dict[str, str] = {
    ".html": "text/html; charset=utf-8",
    ".htm": "text/html; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".png": "image/png",
}

# Worker 侧阶段（§11.3）→ 主服务进度映射（§6）。
_WORKER_STAGE1_PHASES = {"stage1_running", "stage1_done"}
_WORKER_STAGE2_PHASES = {"stage2_running", "completed"}


def _now_utc() -> datetime:
    """naive UTC 时间（与现有 strategy_runs 存储口径一致）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _assume_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _safe_rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


def _load_json_file(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


class UziReportService:
    def __init__(self, *, worker_client: UziWorkerClient | None = None) -> None:
        self._worker = worker_client or uzi_worker_client
        self._queue: queue.Queue[int | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._cancel_events: dict[int, threading.Event] = {}
        self._cancel_lock = threading.Lock()
        # 创建限流（§8 UZI_CREATE_RATE_LIMIT_SECONDS）：client_ip → 上次创建时间。
        self._create_timestamps: dict[str, float] = {}
        self._create_lock = threading.Lock()
        self._source_update_lock = threading.Lock()
        self._source_update_in_progress = False

    def _check_create_rate_limit(self, client_ip: str) -> None:
        """同一登录来源创建新任务的最小间隔；复用任务不在此限（§10.2）。"""
        window = max(0.0, float(get_settings().uzi_create_rate_limit_seconds))
        if window <= 0:
            return
        now = time.monotonic()
        with self._create_lock:
            last = self._create_timestamps.get(client_ip, 0.0)
            if now - last < window:
                raise RuntimeError(
                    "UZI_RATE_LIMITED",
                    "创建请求过于频繁，请稍后再试。",
                )
            self._create_timestamps[client_ip] = now

    def reset_rate_limit(self, client_ip: str | None = None) -> None:
        with self._create_lock:
            if client_ip is None:
                self._create_timestamps.clear()
            else:
                self._create_timestamps.pop(client_ip, None)

    # ── 生命周期 ──────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # 只清掉 stop() 留下的 None 哨兵，保留真实任务（对账入队的恢复任务）。
        drained: list = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                drained.append(item)
        for item in drained:
            self._queue.put(item)
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="uzi-report-executor",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        # 清空残留队列（含哨兵），避免跨测试/重启泄漏旧任务。
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        self._queue.put(None)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        self._thread = None

    def enqueue(self, report_id: int) -> None:
        self._queue.put(report_id)

    # ── 输入与配置校验 ─────────────────────────────────────
    def _validate_ticker_input(self, value: str) -> str:
        cleaned = str(value or "").strip()
        if not cleaned:
            raise ValueError(ERROR_INVALID_TICKER, "股票代码或名称不能为空。")
        if len(cleaned) > _MAX_TICKER_LENGTH:
            raise ValueError(
                ERROR_INVALID_TICKER,
                f"股票代码或名称长度不能超过 {_MAX_TICKER_LENGTH}。",
            )
        if _TICKER_INVALID_RE.search(cleaned):
            raise ValueError(
                ERROR_INVALID_TICKER,
                "股票代码或名称包含不允许的字符。",
            )
        return cleaned

    def _worker_health(self) -> dict[str, Any] | None:
        return self._worker.health()

    def get_source_status(self) -> dict[str, Any]:
        settings = get_settings()
        if not settings.uzi_enabled:
            raise RuntimeError(ERROR_DISABLED, "UZI 模块未启用。")
        payload = self._worker.source_status(check_latest=False)
        if payload is None:
            raise RuntimeError(ERROR_WORKER_UNAVAILABLE, "UZI Worker 不可用。")
        return payload

    def update_source(self, db) -> dict[str, Any]:
        """仅在无活动报告时更新上游，避免运行中切换源码。"""
        settings = get_settings()
        if not settings.uzi_enabled:
            raise RuntimeError(ERROR_DISABLED, "UZI 模块未启用。")
        with self._source_update_lock:
            if self._source_update_in_progress:
                raise RuntimeError(ERROR_UPDATE_BUSY, "UZI 上游更新正在进行。")
            if self._count_active(db) > 0:
                raise RuntimeError(
                    ERROR_UPDATE_BUSY,
                    "有 UZI 报告正在执行，请等待任务结束后再更新。",
                )
            self._source_update_in_progress = True
        try:
            try:
                payload = self._worker.update_source()
            except WorkerJobPayloadError as exc:
                message = str(exc).strip() or "UZI 上游更新失败。"
                code = ERROR_UPDATE_BUSY if "正在执行" in message else ERROR_UPDATE_FAILED
                raise RuntimeError(code, message) from exc
            if payload is None:
                raise RuntimeError(ERROR_WORKER_UNAVAILABLE, "UZI Worker 不可用。")
            return payload
        finally:
            with self._source_update_lock:
                self._source_update_in_progress = False

    def _llm_config_ready(self, app_settings: AppSettings) -> bool:
        base_url = str(getattr(app_settings, "llm_base_url", "") or "").strip()
        api_key = str(getattr(app_settings, "llm_api_key", "") or "").strip()
        return bool(base_url and api_key)

    # ── 创建任务（§5.1 / §10.2）────────────────────────────
    def create_report(
        self,
        db,
        *,
        ticker: str,
        client_ip: str = "unknown",
    ) -> tuple[UziReportJob, bool]:
        with self._source_update_lock:
            if self._source_update_in_progress:
                raise RuntimeError(
                    ERROR_UPDATE_BUSY,
                    "UZI 上游更新正在进行，请稍后创建报告。",
                )
            return self._create_report_locked(
                db,
                ticker=ticker,
                client_ip=client_ip,
            )

    def _create_report_locked(
        self,
        db,
        *,
        ticker: str,
        client_ip: str,
    ) -> tuple[UziReportJob, bool]:
        settings = get_settings()
        if not settings.uzi_enabled:
            raise RuntimeError(ERROR_DISABLED, "UZI 模块未启用。")

        ticker = self._validate_ticker_input(ticker)

        # 同一标准化股票已有非终态任务 → 复用（§5.1 步骤 3）。
        # 复用不消耗创建限流配额。
        existing = self._find_active_by_input(db, ticker)
        if existing is not None:
            return existing, True

        # 创建限流（§8 / §10.2）：仅在真正创建新任务时检查。
        self._check_create_rate_limit(client_ip)
        # LLM 配置完整性：任务开始前校验，422 直返（§13.1），不排队后才失败。
        app_settings = db.scalar(select(AppSettings).order_by(AppSettings.id).limit(1))
        if app_settings is None or not self._llm_config_ready(app_settings):
            raise RuntimeError(
                ERROR_LLM_NOT_CONFIGURED,
                "尚未配置大模型接口（Base URL / API Key），无法生成 UZI 深度报告。",
            )

        # Worker 可用性（§10.2 步骤 2）。
        worker_health = self._worker_health()
        if worker_health is None:
            raise RuntimeError(
                ERROR_WORKER_UNAVAILABLE,
                "UZI Worker 不可用，请确认 aniu-uzi-worker 服务已启动。",
            )
        worker_version = str(
            worker_health.get("uzi_commit") or worker_health.get("worker_version") or ""
        ).strip()

        # 队列容量（§8 UZI_MAX_QUEUED）。
        active_count = self._count_active(db)
        if active_count >= settings.uzi_max_active + settings.uzi_max_queued:
            raise RuntimeError(
                ERROR_QUEUE_FULL,
                "UZI 任务队列已满，请稍后再试。",
            )

        job = UziReportJob(
            ticker_input=ticker,
            status="queued",
            phase="queued",
            progress=0,
            progress_message="任务已入队，等待执行。",
            uzi_commit=worker_version,
            report_rel_dir="",
        )
        db.add(job)
        db.flush()  # 先拿到 id
        job.report_rel_dir = str(job.id)
        db.commit()
        db.refresh(job)

        self.enqueue(job.id)
        return job, False

    def _find_active_by_input(self, db, ticker: str) -> UziReportJob | None:
        stmt = (
            select(UziReportJob)
            .where(
                or_(
                    UziReportJob.ticker_input == ticker,
                    UziReportJob.ticker_normalized == ticker,
                )
            )
            .where(UziReportJob.status.not_in(list(UZI_TERMINAL_STATUSES)))
            .order_by(UziReportJob.created_at.desc())
            .limit(1)
        )
        return db.scalar(stmt)

    def _count_active(self, db) -> int:
        return int(
            db.scalar(
                select(func.count())
                .select_from(UziReportJob)
                .where(UziReportJob.status.not_in(list(UZI_TERMINAL_STATUSES)))
            )
            or 0
        )

    # ── 查询（§10.3 / §10.4）──────────────────────────────
    def list_reports(
        self,
        db,
        *,
        limit: int = 20,
        offset: int = 0,
        ticker: str | None = None,
        status: str | None = None,
    ) -> dict[str, Any]:
        filters = []
        ticker_clean = str(ticker or "").strip()
        if ticker_clean:
            pattern = f"%{ticker_clean}%"
            filters.append(
                or_(
                    UziReportJob.ticker_input.like(pattern),
                    UziReportJob.ticker_normalized.like(pattern),
                    UziReportJob.company_name.like(pattern),
                )
            )
        if status:
            if status not in UZI_REPORT_STATUSES:
                raise ValueError(f"非法的任务状态: {status}")
            filters.append(UziReportJob.status == status)

        total = int(
            db.scalar(
                select(func.count())
                .select_from(UziReportJob)
                .where(*filters)
            )
            or 0
        )
        stmt = (
            select(UziReportJob)
            .where(*filters)
            .order_by(UziReportJob.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        jobs = list(db.scalars(stmt))
        items = [
            self._to_summary(job).model_dump(mode="json") for job in jobs
        ]
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get_report(self, db, report_id: int) -> UziReportDetailRead | None:
        job = db.get(UziReportJob, report_id)
        if job is None:
            return None
        return self._to_detail(job)

    # ── 序列化 ────────────────────────────────────────────
    def _to_summary(self, job: UziReportJob) -> UziReportSummaryRead:
        summary = job.summary_json or {}
        return UziReportSummaryRead(
            id=job.id,
            ticker_input=job.ticker_input,
            ticker_normalized=job.ticker_normalized,
            company_name=job.company_name,
            status=job.status,
            progress=job.progress,
            overall_score=summary.get("overall_score"),
            verdict=summary.get("verdict"),
            llm_model=job.llm_model,
            created_at=_assume_utc(job.created_at) or _now_utc(),
            finished_at=_assume_utc(job.finished_at),
            data_as_of=_assume_utc(job.data_as_of),
        )

    def _to_detail(self, job: UziReportJob) -> UziReportDetailRead:
        summary = self._to_summary(job)
        artifacts = self._clean_artifacts(job)
        return UziReportDetailRead(
            **summary.model_dump(mode="json"),
            phase=job.phase,
            progress_message=job.progress_message,
            error_code=job.error_code,
            error_message=job.error_message,
            uzi_commit=job.uzi_commit,
            llm_reasoning_effort=job.llm_reasoning_effort,
            summary=job.summary_json,
            artifacts=artifacts,
        )

    def _clean_artifacts(self, job: UziReportJob) -> list[UziArtifactRead]:
        """清洗产物清单：只暴露相对文件名/大小/MIME，绝不暴露绝对路径。"""
        manifest = job.artifact_manifest_json or {}
        entries = manifest.get("artifacts")
        if not isinstance(entries, list):
            return []
        cleaned: list[UziArtifactRead] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            file_name = str(entry.get("file") or "").strip()
            if not file_name or "/" in file_name or "\\" in file_name:
                continue
            key = self._artifact_key_for_file(file_name)
            if key is None:
                continue
            cleaned.append(
                UziArtifactRead(
                    key=key,
                    file=file_name,
                    size=int(entry.get("size") or 0),
                    mime=str(entry.get("mime") or ""),
                )
            )
        return cleaned

    @staticmethod
    def _artifact_key_for_file(file_name: str) -> str | None:
        for key, expected in _ARTIFACT_KEY_FILES.items():
            if file_name == expected:
                return key
        return None

    # ── 事件流（§10.5）────────────────────────────────────
    def stream_events(self, report_id: int) -> Iterator[dict[str, Any]]:
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            if job is None:
                yield {"type": "snapshot", "report_id": report_id, "job": None}
                return
            detail = self._to_detail(job).model_dump(mode="json")
            job_status = job.status
        # 先发数据库快照（断线重连语义），再订阅内存事件。
        yield {"type": "snapshot", "report_id": report_id, "job": detail}
        if job_status in UZI_TERMINAL_STATUSES:
            yield {"type": job_status, "report_id": report_id, "job": detail}
            return
        yield from uzi_event_bus.stream(report_id)

    # ── 取消（§10.6）──────────────────────────────────────
    def cancel_report(self, db, report_id: int) -> dict[str, Any]:
        job = db.get(UziReportJob, report_id)
        if job is None:
            raise LookupError("报告任务不存在。")
        if job.status in UZI_TERMINAL_STATUSES:
            return {
                "id": job.id,
                "status": job.status,
                "cancelled": False,
            }

        # LLM 阶段：设置取消事件终止后续调用（§10.6）。
        with self._cancel_lock:
            event = self._cancel_events.setdefault(report_id, threading.Event())
            event.set()

        # 运行中任务向 Worker 发送取消请求（尽力而为）。
        if job.status in {"stage1_running", "stage2_running"}:
            try:
                self._worker.cancel(report_id)
            except Exception:  # noqa: BLE001 - 取消尽力而为
                logger.warning("向 Worker 发送取消失败: report_id=%s", report_id, exc_info=True)

        self._mark_terminal(
            db,
            job,
            status="cancelled",
            error_code=ERROR_CANCELLED,
            error_message="任务已被用户取消。",
        )
        return {"id": job.id, "status": "cancelled", "cancelled": True}

    # ── 删除（§10.7 / §16.1）──────────────────────────────
    def delete_report(self, db, report_id: int) -> None:
        job = db.get(UziReportJob, report_id)
        if job is None:
            raise LookupError("报告任务不存在。")
        if job.status not in UZI_TERMINAL_STATUSES:
            raise RuntimeError("运行中的任务不可删除，请先取消。")

        report_dir = self._resolve_report_dir(job.report_rel_dir)
        # 先删文件，成功后再删数据库记录；失败时不提交 DB 删除（§10.7）。
        if report_dir is not None:
            try:
                _safe_rmtree(report_dir)
            except OSError as exc:
                logger.error("删除报告目录失败: report_id=%s dir=%s %s",
                             report_id, report_dir, exc)
                raise RuntimeError("报告文件删除失败，请稍后重试。") from exc
        db.delete(job)
        db.commit()

    def _resolve_report_dir(self, report_rel_dir: str) -> Path | None:
        """路径解析：必须确认目标是 UZI_REPORT_ROOT 的后代（§16.1）。"""
        relative = str(report_rel_dir or "").strip()
        if not relative:
            return None
        root = get_settings().uzi_report_root.resolve()
        candidate = (root / relative).resolve()
        if not _is_under(candidate, root):
            raise RuntimeError("非法的报告目录。")
        # 拒绝符号链接逃逸：resolve() 已解析软链，若结果不在 root 下即拒绝。
        return candidate

    # ── 产物下载（§10.8 / §16.1 / §16.2）──────────────────
    def get_artifact(
        self,
        db,
        report_id: int,
        artifact_key: str,
    ) -> tuple[Path, str, str | None] | None:
        """返回 (path, mime, download_filename|None)。HTML 默认 inline。"""
        if artifact_key not in UZI_ARTIFACT_KEYS:
            raise LookupError("未知的产物 key。")
        job = db.get(UziReportJob, report_id)
        if job is None:
            raise LookupError("报告任务不存在。")
        if job.status != "completed":
            raise RuntimeError("报告尚未完成，暂无产物。")

        expected_file = _ARTIFACT_KEY_FILES[artifact_key]
        manifest = job.artifact_manifest_json or {}
        mime: str | None = None
        for entry in manifest.get("artifacts") or []:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("file") or "") == expected_file:
                mime = str(entry.get("mime") or "").strip() or None
                break

        report_dir = self._resolve_report_dir(job.report_rel_dir)
        if report_dir is None:
            raise LookupError("报告目录缺失。")
        path = (report_dir / "artifacts" / expected_file).resolve()
        if not _is_under(path, report_dir.resolve()):
            raise RuntimeError("非法的产物路径。")
        if not path.is_file():
            raise LookupError("产物文件不存在。")

        if not mime:
            mime = _MIME_BY_EXTENSION.get(path.suffix.lower(), "application/octet-stream")
        download_name: str | None = None
        if artifact_key != "html":
            download_name = expected_file
        return path, mime, download_name

    # ── 执行循环（§5 / §6）────────────────────────────────
    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                report_id = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if report_id is None:
                break
            try:
                self._run_job(report_id)
            except Exception:  # noqa: BLE001 - 单任务失败不拖垮执行器
                logger.exception("UZI 任务执行异常: report_id=%s", report_id)

    def _run_job(self, report_id: int) -> None:
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            if job is None:
                return
            if job.status in UZI_TERMINAL_STATUSES:
                return
            app_settings = db.scalar(
                select(AppSettings).order_by(AppSettings.id).limit(1)
            )
            if app_settings is None:
                self._mark_terminal(
                    db, job, "failed",
                    error_code=ERROR_LLM_NOT_CONFIGURED,
                    error_message="缺少系统设置，无法执行。",
                )
                return
            job.started_at = job.started_at or _now_utc()
            job.llm_model = app_settings.llm_model
            job.llm_reasoning_effort = app_settings.llm_reasoning_effort
            # 记录恢复起始状态：从对应阶段继续，而非无条件重跑 Stage 1（阻断项7）。
            resume_status = str(job.status or "queued").strip()
            db.commit()

        deadline = time.monotonic() + float(get_settings().uzi_job_timeout_seconds)

        try:
            # 恢复语义：已过 Stage1 的不重跑；LLM 评审未完成的从评审继续；
            # Stage2 进行中的继续 Stage2。queued/未知状态走完整流程。
            if resume_status in {"queued", "stage1_running"}:
                self._run_stage1(report_id, deadline=deadline)
                self._check_timeout(deadline, report_id)
                self.run_llm_review(report_id, deadline=deadline)
            elif resume_status == "llm_review":
                self.run_llm_review(report_id, deadline=deadline)
            # stage2_running 直接进入 stage2
            self._check_timeout(deadline, report_id)
            if resume_status in {"queued", "stage1_running", "llm_review"}:
                self._run_stage2(report_id, deadline=deadline)
            elif resume_status == "stage2_running":
                self._run_stage2(report_id, deadline=deadline)
            self._finalize_completed(report_id)
        except _JobCancelled:
            with session_scope() as db:
                job = db.get(UziReportJob, report_id)
                if job is not None and job.status not in UZI_TERMINAL_STATUSES:
                    self._mark_terminal(
                        db, job,
                        status="cancelled",
                        error_code=ERROR_CANCELLED,
                        error_message="任务已被取消。",
                    )
        except _JobFailed as exc:
            # 超时/失败时，若任务可能在 Worker 运行，尽力终止 Worker 子进程，
            # 防止残留进程继续写文件（§17.3 / review P1）。
            self._cancel_worker_best_effort(report_id)
            with session_scope() as db:
                job = db.get(UziReportJob, report_id)
                if job is not None and job.status not in UZI_TERMINAL_STATUSES:
                    self._mark_terminal(
                        db, job,
                        status="failed",
                        error_code=exc.error_code,
                        error_message=exc.message,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.exception("UZI 任务未捕获异常: report_id=%s", report_id)
            # 兜底：同样尽力终止 Worker 子进程。
            self._cancel_worker_best_effort(report_id)
            with session_scope() as db:
                job = db.get(UziReportJob, report_id)
                if job is not None and job.status not in UZI_TERMINAL_STATUSES:
                    self._mark_terminal(
                        db, job,
                        status="failed",
                        error_code=ERROR_ORPHANED_JOB,
                        error_message=f"任务执行异常：{exc}",
                    )

    def _run_stage1(self, report_id: int, *, deadline: float) -> None:
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            self._raise_if_cancelled(report_id)
            self._update_progress(
                db, job,
                status="stage1_running", phase="stage1_running", progress=5,
                message="Worker 接受任务，开始数据采集与机械评分。",
            )
            mx_api_key = db.scalar(
                select(AppSettings).order_by(AppSettings.id).limit(1)
            ).mx_api_key

        # 恢复语义（§17.1）：先查询 Worker 已有任务状态，避免重启后重复提交。
        worker_state = self._worker.get_job(report_id)
        if worker_state is not None:
            status = str(worker_state.get("status") or "").strip()
            phase = str(worker_state.get("phase") or "").strip()
            # Stage 1 完成标志：worker status=succeeded 且本地已有 stage1-manifest.json
            stage1_manifest = self._read_stage1_manifest(report_id)
            stage1_done = (
                stage1_manifest is not None
                and status == "succeeded"
                and phase == "stage1_done"
            )
            if stage1_done:
                # Worker 已完成 Stage 1：跳过提交，直接进入 LLM 评审。
                self._sync_worker_progress(report_id, worker_state)
                return
            if status in {"accepted", "running"}:
                # Worker 仍在执行：只轮询，不重复提交。
                self._sync_worker_progress(report_id, worker_state)
                self._poll_worker(
                    report_id,
                    until={"succeeded", "failed", "cancelled"},
                    deadline=deadline,
                )
                return
            if status in {"failed", "cancelled"}:
                raise _JobFailed(
                    ERROR_STAGE1_FAILED if status == "failed" else ERROR_CANCELLED,
                    str(
                        worker_state.get("error_message")
                        or f"Worker Stage 1 已{status}。"
                    ),
                )
        else:
            # Worker 不认识该任务：若本地已有 Stage 1 清单则从产物推进，
            # 否则视为新任务正常提交。
            stage1_manifest = self._read_stage1_manifest(report_id)
            if stage1_manifest is not None:
                logger.info(
                    "重启恢复：Worker 不认识任务但本地已有 Stage 1 产物，"
                    "跳过 Stage 1 直接推进: report_id=%s",
                    report_id,
                )
                return

        # 正常提交；若 Worker 返回"任务已存在"等业务错误，转查询状态继续。
        try:
            payload = self._worker.submit_stage1(
                report_id=report_id,
                ticker=job.ticker_input,
                report_rel_dir=str(report_id),
                mx_api_key=mx_api_key,
            )
        except WorkerJobPayloadError as exc:
            logger.warning(
                "Stage 1 提交被拒绝，转查询 Worker 状态继续: report_id=%s %s",
                report_id, exc,
            )
            worker_state = self._worker.get_job(report_id)
            if worker_state is not None:
                status = str(worker_state.get("status") or "").strip()
                phase = str(worker_state.get("phase") or "").strip()
                if status == "succeeded" and phase == "stage1_done":
                    self._sync_worker_progress(report_id, worker_state)
                    return
                if status in {"accepted", "running"}:
                    self._poll_worker(
                        report_id,
                        until={"succeeded", "failed", "cancelled"},
                        deadline=deadline,
                    )
                    return
                if status in {"failed", "cancelled"}:
                    raise _JobFailed(
                        ERROR_STAGE1_FAILED if status == "failed" else ERROR_CANCELLED,
                        str(
                            worker_state.get("error_message")
                            or f"Worker Stage 1 已{status}。"
                        ),
                    ) from exc
            raise _JobFailed(ERROR_STAGE1_FAILED, f"Stage 1 提交失败：{exc}") from exc
        if payload is None:
            raise _JobFailed(ERROR_WORKER_UNAVAILABLE, "UZI Worker 不可用，Stage 1 提交失败。")

        self._poll_worker(report_id, until={"succeeded", "failed", "cancelled"}, deadline=deadline)

    def _read_stage1_manifest(self, report_id: int) -> dict[str, Any] | None:
        """读取本地 Stage 1 清单；不存在或损坏时返回 None。"""
        try:
            report_dir = self._resolve_report_dir(str(report_id))
        except RuntimeError:
            return None
        if report_dir is None:
            return None
        return _load_json_file(report_dir / "work" / "stage1-manifest.json")

    def _read_stage1_ticker(self, report_id: int) -> str | None:
        """从 Stage 1 清单读取标准化股票代码（供 Stage 2 一致性校验）。"""
        try:
            report_dir = self._resolve_report_dir(str(report_id))
        except RuntimeError:
            return None
        if report_dir is None:
            return None
        manifest = _load_json_file(report_dir / "work" / "stage1-manifest.json")
        if manifest is None:
            return None
        value = str(manifest.get("ticker_normalized") or "").strip()
        return value or None

    def _poll_worker(self, report_id: int, *, until: set[str], deadline: float) -> None:
        """轮询 Worker 任务状态（§11.3），同步进度到 DB 与事件总线。

        超过总 deadline 时按任务超时失败，避免 Worker 永久 running 导致
        单线程执行器阻塞后续所有报告。
        """
        poll_seconds = max(0.5, float(get_settings().uzi_poll_interval_seconds))
        while True:
            self._raise_if_cancelled(report_id)
            if time.monotonic() > deadline:
                raise _JobFailed(
                    ERROR_JOB_TIMEOUT,
                    "任务超时（Worker 长时间未完成）。",
                )
            worker_state = self._worker.get_job(report_id)
            if worker_state is None:
                time.sleep(poll_seconds)
                continue
            status = str(worker_state.get("status") or "").strip()
            self._sync_worker_progress(report_id, worker_state)
            if status in until:
                if status == "succeeded":
                    return
                error_code = str(worker_state.get("error_code") or "") or (
                    ERROR_STAGE1_FAILED
                    if "stage1" in str(worker_state.get("phase") or "")
                    else ERROR_STAGE2_FAILED
                )
                raise _JobFailed(
                    error_code,
                    str(worker_state.get("error_message") or "Worker 任务失败。"),
                )
            time.sleep(poll_seconds)

    def _sync_worker_progress(self, report_id: int, worker_state: dict[str, Any]) -> None:
        phase = str(worker_state.get("phase") or "").strip()
        progress = int(worker_state.get("progress") or 0)
        message = str(worker_state.get("progress_message") or "").strip() or None
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            if job is None or job.status in UZI_TERMINAL_STATUSES:
                return
            if phase in _WORKER_STAGE1_PHASES and job.status == "stage1_running":
                self._update_progress(
                    db, job,
                    status="stage1_running", phase=phase, progress=progress,
                    message=message,
                )
            elif phase in _WORKER_STAGE2_PHASES and job.status == "stage2_running":
                self._update_progress(
                    db, job,
                    status="stage2_running", phase=phase, progress=progress,
                    message=message,
                )
            elif phase in _WORKER_STAGE1_PHASES and job.status == "queued":
                self._update_progress(
                    db, job,
                    status="stage1_running", phase=phase, progress=progress,
                    message=message,
                )

    # ── LLM 深度评审（§13，批次3 接入）────────────────────
    def run_llm_review(self, report_id: int, *, deadline: float | None = None) -> None:
        """AniU 大模型深度评审（文档 §5.3 / §13）。

        ``deadline``（可选）：monotonic 时间戳；LLM 评审的多轮子任务、
        工具调用与重试受总任务 deadline 约束（§13.5 / review P1）。

        默认调用 ``UziLlmOrchestrator`` 执行多轮结构化评审并写入
        ``work/agent_analysis.json``（``agent_reviewed=true``）。

        ``UZI_LLM_REVIEW_MOCK=1`` 仅为测试/联调逃生口：写入最小合法
        ``agent_analysis.json`` 以便推进 Stage 2，内容标注为 mock 评审，
        禁止用于生产（§5.3 禁止空壳冒充）。
        """
        if os.environ.get("UZI_LLM_REVIEW_MOCK", "0") in {"1", "true", "True"}:
            self._write_mock_llm_review(report_id)
            return

        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            self._raise_if_cancelled(report_id)
            self._update_progress(
                db, job,
                status="llm_review", phase="llm_review", progress=50,
                message="LLM 评审开始（投资者与定性研究）。",
            )
            app_settings = db.scalar(
                select(AppSettings).order_by(AppSettings.id).limit(1)
            )
        if app_settings is None:
            raise _JobFailed(ERROR_LLM_NOT_CONFIGURED, "缺少系统设置，无法执行深度评审。")
        if not self._llm_config_ready(app_settings):
            raise _JobFailed(
                ERROR_LLM_NOT_CONFIGURED,
                "尚未配置大模型接口（Base URL / API Key），无法执行深度评审。",
            )

        with self._cancel_lock:
            cancel_event = self._cancel_events.get(report_id)

        def _progress(progress: int, message: str) -> None:
            with session_scope() as db:
                fresh = db.get(UziReportJob, report_id)
                if fresh is None or fresh.status in UZI_TERMINAL_STATUSES:
                    return
                self._update_progress(
                    db, fresh,
                    status="llm_review", phase="llm_review",
                    progress=progress, message=message,
                )

        try:
            UziLlmOrchestrator().run(
                report_id=report_id,
                app_settings=app_settings,
                report_root=get_settings().uzi_report_root,
                progress=_progress,
                cancel_event=cancel_event,
                deadline=deadline,
            )
        except UziReviewCancelled:
            raise _JobCancelled() from None
        except UziReviewError as exc:
            logger.warning(
                "UZI LLM 评审失败: report_id=%s code=%s %s",
                report_id, exc.error_code, exc.message,
            )
            raise _JobFailed(exc.error_code, exc.message) from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("UZI LLM 评审异常: report_id=%s", report_id)
            raise _JobFailed(ERROR_LLM_REVIEW_FAILED, f"深度评审执行异常：{exc}") from exc

    def _write_mock_llm_review(self, report_id: int) -> None:
        """测试/联调逃生口：写入符合上游 schema 的最小合法 agent_analysis.json。

        从 panel.json 读取真实投资者列表生成 per_investor_override，确保
        mock 评审也覆盖投资者卡片（review 问题5），不会因空壳被掊截。
        """
        report_dir = self._resolve_report_dir(str(report_id))
        if report_dir is None:
            raise _JobFailed(ERROR_ORPHANED_JOB, "报告目录缺失。")
        work_dir = report_dir / "work"
        work_dir.mkdir(parents=True, exist_ok=True)
        # 从 panel.json 抽取真实投资者生成 per_investor_override（review 问题5）
        panel = _load_json_file(work_dir / "panel.json") or {}
        investors = panel.get("investors") or []
        per_investor_override: dict[str, Any] = {}
        if isinstance(investors, list):
            for inv in investors[:8]:
                if not isinstance(inv, dict):
                    continue
                inv_id = str(inv.get("investor_id") or "").strip()
                if not inv_id:
                    continue
                per_investor_override[inv_id] = {
                    "signal": inv.get("signal") or "neutral",
                    "score": inv.get("score") or 60,
                    "headline": f"[mock] {inv.get('name', inv_id)} 评审占位",
                    "reasoning": "[mock 评审] 测试联调用占位评语，需真实 LLM 评审。",
                    "comment": f"[mock] {inv.get('name', inv_id)} 占位。",
                    "verdict": inv.get("verdict") or "观望",
                }
        agent_analysis = {
            "agent_reviewed": True,
            "mock_review": True,
            "schema_version": 1,
            "dim_commentary": {
                "0_basic": "[mock 评审] 公司基本面稳健，营收利润保持增长。测试联调用占位评语。",
                "1_financials": "[mock 评审] ROE 保持稳定，现金流充裕。测试联调用占位评语。",
            },
            "panel_insights": "[mock 评审] 评委投票多头略占优，价值派与成长派分歧在估值中枢。测试联调用占位内容。",
            "great_divide_override": {
                "punchline": "[mock] 价值派看多但成长派担心增速回落。",
                "bull_say_rounds": ["[mock] 估值低", "[mock] 现金流稳", "[mock] 股息高"],
                "bear_say_rounds": ["[mock] 增速放缓", "[mock] 竞争加剧", "[mock] 宏观不确定"],
            },
            "narrative_override": {
                "core_conclusion": "[mock 评审] 综合基本面与情绪，中长期价值较高。测试联调用占位结论。",
                "risks": ["[mock] 行业政策变化", "[mock] 原材料波动", "[mock] 市场风格切换"],
                "buy_zones": {
                    "value": {"price": 1650, "rationale": "[mock] 低估值区间"},
                    "growth": {"price": 1700, "rationale": "[mock] 成长合理"},
                    "technical": {"price": 1680, "rationale": "[mock] 技术支撑"},
                    "youzi": {"price": 1720, "rationale": "[mock] 情绪驱动"},
                },
            },
            "qualitative_deep_dive": {
                "3_macro": {
                    "evidence": [{"source": "[mock]", "url": "", "finding": "[mock] 流动性宽松"}],
                    "associations": [],
                    "conclusion": "[mock] 宏观环境友好。",
                },
                "7_industry": {
                    "evidence": [{"source": "[mock]", "url": "", "finding": "[mock] 行业景气上行"}],
                    "associations": [],
                    "conclusion": "[mock] 行业空间广阔。",
                },
                "13_policy": {
                    "evidence": [{"source": "[mock]", "url": "", "finding": "[mock] 政策中性"}],
                    "associations": [],
                    "conclusion": "[mock] 政策无扰动。",
                },
            },
            "data_gap_acknowledged": {
                "8_materials": "[mock] 原材料成本明细未获取。"
            },
            "per_investor_override": per_investor_override,
        }
        (work_dir / "agent_analysis.json").write_text(
            json.dumps(agent_analysis, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            if job is not None:
                self._update_progress(
                    db, job,
                    status="llm_review", phase="llm_review", progress=50,
                    message="大模型深度评审（测试占位）。",
                )

    def _run_stage2(self, report_id: int, *, deadline: float) -> None:
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            self._raise_if_cancelled(report_id)
            self._update_progress(
                db, job,
                status="stage2_running", phase="stage2_running", progress=85,
                message="Stage 2 开始综合与报告渲染。",
            )

        # 恢复语义（§17.1）：先查询 Worker 已有任务状态，避免重启后重复提交。
        # 注意：Worker 的 job state 是 stage1/stage2 共享的，
        # status=succeeded 可能是 Stage 1 完成（而非 Stage 2）。
        # 只有 phase=completed 且所有关键产物齐全时才允许直接落库。
        worker_state = self._worker.get_job(report_id)
        stage2_done = self._stage2_artifacts_complete(report_id)
        if worker_state is not None:
            status = str(worker_state.get("status") or "").strip()
            phase = str(worker_state.get("phase") or "").strip()
            if status == "succeeded":
                if phase == "completed" and stage2_done:
                    # Stage 2 确已完成：跳过提交，直接落库。
                    self._sync_worker_progress(report_id, worker_state)
                    return
                # status=succeeded 但无 Stage 2 产物：Stage 1 已完成，
                # 需要 submit_stage2，不跳过。
            elif status in {"accepted", "running"}:
                # Worker 仍在执行：只轮询，不重复提交。轮询结束后必须
                # 重新检查 phase；Stage 1 完成时还要继续提交 Stage 2。
                self._sync_worker_progress(report_id, worker_state)
                self._poll_worker(
                    report_id,
                    until={"succeeded", "failed", "cancelled"},
                    deadline=deadline,
                )
                worker_state = self._worker.get_job(report_id)
                status = str((worker_state or {}).get("status") or "").strip()
                phase = str((worker_state or {}).get("phase") or "").strip()
                stage2_done = self._stage2_artifacts_complete(report_id)
                if status == "succeeded" and phase == "completed" and stage2_done:
                    self._sync_worker_progress(report_id, worker_state)
                    return
                if status != "succeeded" or phase != "stage1_done":
                    raise _JobFailed(
                        ERROR_STAGE2_FAILED,
                        "Worker Stage 2 状态未完成且无法恢复。",
                    )
            if status in {"failed", "cancelled"}:
                raise _JobFailed(
                    ERROR_STAGE2_FAILED if status == "failed" else ERROR_CANCELLED,
                    str(
                        worker_state.get("error_message")
                        or f"Worker Stage 2 已{status}。"
                    ),
                )

        # 从 Stage 1 清单读取标准化代码，供 Worker 校验与 Stage 1 一致（§11.4）。
        normalized_ticker = self._read_stage1_ticker(report_id)
        try:
            payload = self._worker.submit_stage2(
                report_id=report_id, ticker=normalized_ticker
            )
        except WorkerJobPayloadError as exc:
            logger.warning(
                "Stage 2 提交被拒绝，转查询 Worker 状态继续: report_id=%s %s",
                report_id, exc,
            )
            worker_state = self._worker.get_job(report_id)
            if worker_state is not None:
                status = str(worker_state.get("status") or "").strip()
                phase = str(worker_state.get("phase") or "").strip()
                if (
                    status == "succeeded"
                    and phase == "completed"
                    and self._stage2_artifacts_complete(report_id)
                ):
                    self._sync_worker_progress(report_id, worker_state)
                    return
                if status in {"accepted", "running"}:
                    self._poll_worker(
                        report_id,
                        until={"succeeded", "failed", "cancelled"},
                        deadline=deadline,
                    )
                    worker_state = self._worker.get_job(report_id)
                    if (
                        worker_state is not None
                        and str(worker_state.get("status") or "").strip() == "succeeded"
                        and str(worker_state.get("phase") or "").strip() == "completed"
                        and self._stage2_artifacts_complete(report_id)
                    ):
                        self._sync_worker_progress(report_id, worker_state)
                        return
            raise _JobFailed(ERROR_STAGE2_FAILED, f"Stage 2 提交失败：{exc}") from exc
        if payload is None:
            raise _JobFailed(ERROR_STAGE2_FAILED, "Stage 2 提交失败（Worker 不可达）。")
        self._poll_worker(report_id, until={"succeeded", "failed", "cancelled"}, deadline=deadline)

    def _finalize_completed(self, report_id: int) -> None:
        """从 synthesis.json 与 manifest 提取标准化摘要，落库并 completed。"""
        report_dir = self._resolve_report_dir(str(report_id))
        if report_dir is None:
            raise _JobFailed(ERROR_ORPHANED_JOB, "报告目录缺失。")
        artifacts_dir = report_dir / "artifacts"
        synthesis = _load_json_file(artifacts_dir / "synthesis.json")
        manifest = _load_json_file(artifacts_dir / "artifact-manifest.json")
        if synthesis is None:
            raise _JobFailed(ERROR_ARTIFACT_INVALID, "synthesis.json 缺失或格式错误。")
        if manifest is None:
            raise _JobFailed(ERROR_ARTIFACT_INVALID, "artifact-manifest.json 缺失。")

        summary = self._build_summary(synthesis, manifest, report_dir=report_dir)
        with session_scope() as db:
            job = db.get(UziReportJob, report_id)
            if job is None or job.status in UZI_TERMINAL_STATUSES:
                return
            job.ticker_normalized = (
                str(synthesis.get("ticker") or job.ticker_normalized or "").strip()
                or None
            )
            job.company_name = (
                str(synthesis.get("company_name") or synthesis.get("name") or "").strip()
                or None
            )
            job.summary_json = summary
            job.artifact_manifest_json = manifest
            # 与 summary_json 保持同一回退语义：Stage 2 没有 data_as_of 时，
            # 使用 Stage 1 manifest 的数据时间，避免列表和详情返回不一致。
            job.data_as_of = self._parse_data_as_of(summary.get("data_as_of"))
            self._update_progress(
                db, job,
                status="completed", phase="completed", progress=100,
                message="报告已生成并校验通过。",
                final=True,
            )

    def _build_summary(
        self,
        synthesis: dict[str, Any],
        manifest: dict[str, Any],
        report_dir: Path | None = None,
    ) -> dict[str, Any]:
        """标准化摘要结构（文档 §9.1），按上游真实字段路径归一化。

        上游产物真实结构（commit 7bc779d，已验证）：
        - 估值在 ``synthesis.institutional_modeling``（含
          initiating_rating/target_price/upside_pct/dcf_verdict 等）
        - 催化剂在 ``synthesis.dashboard.intelligence.catalysts``
        - 数据缺口在 ``synthesis.data_gaps``（含 coverage_pct/total_gaps/
          unresolved/tasks）
        - 投资者统计在 ``panel.json.signal_distribution``（需从 work 目录读）
        - 一句话在 ``artifacts/one-liner.txt``
        - 定性评审在 ``agent_analysis.json`` 的 qualitative_deep_dive
        """
        score = synthesis.get("overall_score")
        try:
            score = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            score = 0.0

        # 估值：institutional_modeling
        inst = synthesis.get("institutional_modeling") or {}
        valuation_methods: list[str] = []
        for key in ("dcf_verdict", "lbo_verdict", "comps_verdict"):
            val = inst.get(key)
            if val:
                valuation_methods.append(f"{key}:{val}")
        target_price = inst.get("target_price")
        upside_pct = inst.get("upside_pct")

        # 催化剂：dashboard.intelligence.catalysts
        dashboard = synthesis.get("dashboard") or {}
        intelligence = dashboard.get("intelligence") or {}
        catalysts = list(intelligence.get("catalysts") or [])

        # 数据缺口：synthesis.data_gaps（stage2 从 _data_gaps.json 合并）
        dg = synthesis.get("data_gaps") or {}
        data_gap_items = [
            self._format_data_gap_item(item)
            for item in (dg.get("tasks") or [])
        ]
        coverage_pct = dg.get("coverage_pct")
        try:
            coverage_pct = float(coverage_pct) if coverage_pct is not None else 0.0
        except (TypeError, ValueError):
            coverage_pct = 0.0
        unresolved = int(dg.get("unresolved") or 0)

        # 投资者统计：panel.json.signal_distribution（需从 work 目录读）
        panel_bullish = panel_neutral = panel_bearish = 0
        key_disagreements: list[str] = []
        qualitative: dict[str, Any] = {}
        if report_dir is not None:
            panel = _load_json_file(report_dir / "work" / "panel.json") or {}
            sig_dist = panel.get("signal_distribution") or {}
            panel_bullish = int(sig_dist.get("bullish") or 0)
            panel_neutral = int(sig_dist.get("neutral") or 0)
            panel_bearish = int(sig_dist.get("bearish") or 0)
            # school_scores 提供流派级分歧
            school_scores = panel.get("school_scores") or {}
            for _g, _sc in school_scores.items():
                if isinstance(_sc, dict):
                    verdict = str(_sc.get("verdict") or "").strip()
                    if verdict:
                        label = str(_sc.get("label") or _g).strip()
                        key_disagreements.append(f"{label}:{verdict}")
            # 定性评审：agent_analysis.json.qualitative_deep_dive
            agent_aa = _load_json_file(report_dir / "work" / "agent_analysis.json") or {}
            qdd = agent_aa.get("qualitative_deep_dive")
            if isinstance(qdd, dict):
                qualitative = qdd
            # 也可从 panel_insights 补充定性叙述
            pi = agent_aa.get("panel_insights")
            if isinstance(pi, str) and pi.strip():
                qualitative.setdefault("panel_insights", pi.strip())

        # one-liner：artifacts/one-liner.txt
        one_liner = ""
        if report_dir is not None:
            ol_path = report_dir / "artifacts" / "one-liner.txt"
            try:
                if ol_path.is_file():
                    one_liner = ol_path.read_text(encoding="utf-8").strip()
            except OSError:
                one_liner = ""
        # dashboard.core_conclusion 作为 one_liner 兑底
        if not one_liner:
            one_liner = str(dashboard.get("core_conclusion") or "").strip()

        # data_as_of：stage1-manifest.data_as_of（finalize 中 synthesis 通常无此字段）
        data_as_of = str(synthesis.get("data_as_of") or "").strip()
        if not data_as_of and report_dir is not None:
            s1m = _load_json_file(report_dir / "work" / "stage1-manifest.json") or {}
            data_as_of = str(s1m.get("data_as_of") or "").strip()

        return {
            "schema_version": 1,
            "ticker": str(synthesis.get("ticker") or "").strip(),
            "company_name": str(
                synthesis.get("company_name") or synthesis.get("name") or ""
            ).strip(),
            "overall_score": score,
            "verdict": str(synthesis.get("verdict_label") or "").strip(),
            "one_liner": one_liner,
            "valuation": {
                "rating": str(inst.get("initiating_rating") or "").strip(),
                "target_price": self._safe_float(target_price),
                "upside_pct": self._safe_float(upside_pct),
                "methods": valuation_methods,
            },
            "risks": list(synthesis.get("risks") or []),
            "catalysts": catalysts,
            "panel": {
                "bullish": panel_bullish,
                "neutral": panel_neutral,
                "bearish": panel_bearish,
                "key_disagreements": key_disagreements,
            },
            "qualitative": qualitative,
            "data_gaps": {
                "coverage_pct": coverage_pct,
                "unresolved": unresolved,
                "items": data_gap_items,
            },
            "sources": list(synthesis.get("sources") or []),
            "data_as_of": data_as_of,
            "generated_at": str(manifest.get("generated_at") or ""),
            "disclaimer": "历史研究资料，不构成投资建议",
        }

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value) if value is not None else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _format_data_gap_item(item: Any) -> str:
        if isinstance(item, str):
            return item.strip()
        if isinstance(item, dict):
            dim = str(item.get("dim") or item.get("dimension") or "").strip()
            field = str(item.get("field") or item.get("item") or "").strip()
            severity = str(item.get("severity") or "").strip()
            detail = str(item.get("reason") or item.get("message") or "").strip()
            label = ".".join(part for part in (dim, field) if part)
            parts = [part for part in (label, detail) if part]
            if severity:
                parts.append(f"严重性：{severity}")
            return "；".join(parts) or "未命名数据缺口"
        return str(item).strip()

    @staticmethod
    def _parse_data_as_of(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return _assume_utc(parsed)

    # ── 状态更新工具 ──────────────────────────────────────
    def _update_progress(
        self,
        db,
        job: UziReportJob,
        *,
        status: str,
        phase: str,
        progress: int,
        message: str | None,
        final: bool = False,
    ) -> None:
        previous_status = job.status
        job.status = status
        job.phase = phase
        job.progress = max(0, min(100, int(progress)))
        job.progress_message = message
        if final:
            job.finished_at = _now_utc()
        db.commit()
        uzi_event_bus.publish(
            job.id,
            "progress",
            {
                "status": status,
                "phase": phase,
                "progress": job.progress,
                "message": message,
            },
        )
        if status != previous_status:
            uzi_event_bus.publish(
                job.id,
                "status_changed",
                {
                    "from": previous_status,
                    "to": status,
                    "progress": job.progress,
                },
            )
        if final:
            uzi_event_bus.publish(
                job.id,
                status,
                {"progress": job.progress, "message": message},
            )

    def _mark_terminal(
        self,
        db,
        job: UziReportJob,
        *,
        status: str,
        error_code: str | None,
        error_message: str | None,
    ) -> None:
        previous_status = job.status
        job.status = status
        job.error_code = error_code
        job.error_message = error_message
        job.finished_at = _now_utc()
        db.commit()
        uzi_event_bus.publish(
            job.id,
            "status_changed",
            {"from": previous_status, "to": status},
        )
        uzi_event_bus.publish(
            job.id,
            status,
            {"progress": job.progress, "message": error_message},
        )

    def _raise_if_cancelled(self, report_id: int) -> None:
        with self._cancel_lock:
            event = self._cancel_events.get(report_id)
            if event is not None and event.is_set():
                raise _JobCancelled()

    def _cancel_worker_best_effort(self, report_id: int) -> None:
        """尽力向 Worker 发送取消请求；失败仅记录日志，不阻断主流程。"""
        try:
            self._worker.cancel(report_id)
        except (WorkerJobPayloadError, Exception):  # noqa: BLE001 - 取消尽力而为
            logger.warning(
                "向 Worker 发送取消失败（best-effort）: report_id=%s",
                report_id,
                exc_info=True,
            )

    @staticmethod
    def _check_timeout(deadline: float, report_id: int) -> None:
        if time.monotonic() > deadline:
            raise _JobFailed(ERROR_JOB_TIMEOUT, "任务执行超时。")

    # ── 启动对账（§17.1）──────────────────────────────────
    def reconcile_on_startup(self, db) -> None:
        """启动时查询所有非终态任务，按 Worker 状态恢复或标记失败。"""
        jobs = list(
            db.scalars(
                select(UziReportJob).where(
                    UziReportJob.status.not_in(list(UZI_TERMINAL_STATUSES))
                )
            )
        )
        if not jobs:
            return

        worker_health = self._worker_health()
        if worker_health is None:
            # Worker 不可达：无法确认一致状态，保守标记失败（§17.1 第 5 条）。
            for job in jobs:
                self._mark_terminal(
                    db, job,
                    status="failed",
                    error_code=ERROR_ORPHANED_JOB,
                    error_message="主服务重启时 UZI Worker 不可达，任务状态无法确认。",
                )
            db.commit()
            return

        for job in jobs:
            worker_state = self._worker.get_job(job.id)
            if worker_state is None:
                # Worker 不认识任务：若本地有完整阶段产物则从清单恢复，否则失败。
                if self._has_completed_artifacts(job):
                    self._resume_from_artifacts(db, job)
                else:
                    self._mark_terminal(
                        db, job,
                        status="failed",
                        error_code=ERROR_ORPHANED_JOB,
                        error_message="主服务重启后 Worker 无法确认该任务状态。",
                    )
                db.commit()
                continue

            worker_status = str(worker_state.get("status") or "").strip()
            worker_phase = str(worker_state.get("phase") or "").strip()
            if worker_status in {"failed", "cancelled"}:
                self._mark_terminal(
                    db, job,
                    status=worker_status,
                    error_code=str(worker_state.get("error_code") or "") or None,
                    error_message=str(worker_state.get("error_message") or "")
                    or ("Worker 任务已失败。" if worker_status == "failed" else "Worker 任务已取消。"),
                )
                db.commit()
            elif worker_status == "succeeded" and worker_phase == "stage1_done":
                # Worker 已完成 Stage 1，重新入队继续 LLM 评审。
                with session_scope() as inner_db:
                    fresh = inner_db.get(UziReportJob, job.id)
                    if fresh is not None:
                        self._update_progress(
                            inner_db, fresh,
                            status="llm_review", phase="llm_review", progress=50,
                            message="重启恢复：Stage 1 已完成，继续深度评审。",
                        )
                self.enqueue(job.id)
            else:
                # Worker 仍在执行（accepted/running）或已成功进入 stage2。
                self.enqueue(job.id)

    def _has_completed_artifacts(self, job: UziReportJob) -> bool:
        try:
            report_dir = self._resolve_report_dir(job.report_rel_dir)
        except RuntimeError:
            return False
        if report_dir is None:
            return False
        return self._stage2_artifacts_complete_at(report_dir)

    def _stage2_artifacts_complete(self, report_id: int) -> bool:
        try:
            report_dir = self._resolve_report_dir(str(report_id))
        except RuntimeError:
            return False
        return report_dir is not None and self._stage2_artifacts_complete_at(report_dir)

    @staticmethod
    def _stage2_artifacts_complete_at(report_dir: Path) -> bool:
        artifacts = report_dir / "artifacts"
        return all(
            (artifacts / file_name).is_file()
            for file_name in (
                "synthesis.json",
                "artifact-manifest.json",
                "full-report-standalone.html",
            )
        )

    def _resume_from_artifacts(self, db, job: UziReportJob) -> None:
        """本地已有完整产物：从清单恢复为 completed。"""
        try:
            self._finalize_completed(job.id)
        except _JobFailed as exc:
            self._mark_terminal(
                db, job,
                status="failed",
                error_code=exc.error_code,
                error_message=exc.message,
            )


class _JobFailed(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class _JobCancelled(RuntimeError):
    pass


uzi_report_service = UziReportService()
