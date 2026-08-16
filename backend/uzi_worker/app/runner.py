"""任务执行器：隔离工作目录、Stage 1/2 子进程、取消与产物校验。

目录结构遵循文档 §12.2：

.. code:: text

    {UZI_REPORT_ROOT}/{report_id}/
    ├── worker-state.json
    ├── logs/{stage1.log, stage2.log}
    ├── work/{uzi/, .cache/, stage1-manifest.json, agent_analysis.json}
    ├── artifacts.tmp/
    └── artifacts/...

执行方式（§12.3）：受控子进程调用，``shell=False``，用户输入只作为
函数参数传递；子进程以独立进程组运行（``start_new_session=True``），
取消时对整组发 SIGTERM → 等待 ≤10s → SIGKILL。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from app.config import get_stage_env
from app.models import (
    ARTIFACTS_REL,
    AGENT_ANALYSIS_REL,
    STAGE1_MANIFEST_REL,
    ERROR_ARTIFACT_INVALID,
    ERROR_STAGE1_FAILED,
    ERROR_STAGE2_FAILED,
    WorkerJobState,
    WorkerStatus,
)
from app.sanitize import register_secret, sanitize_error_message, unregister_secret
from app.state_store import StateStore

logger = logging.getLogger(__name__)

_REPORT_ID_RE = re.compile(r"^\d{1,10}$")
_CANCEL_GRACE_SECONDS = 10
_MONITOR_POLL_SECONDS = 0.5
_MIN_HTML_BYTES = 10 * 1024


class JobAlreadyExistsError(RuntimeError):
    pass


class StageGuardError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.message = message


class SourceMissingError(RuntimeError):
    pass


class JobRunner:
    def __init__(
        self,
        *,
        store: StateStore,
        report_root: Path,
        source_root: Path,
        mock: bool = False,
    ) -> None:
        self._store = store
        self._report_root = Path(report_root)
        self._source_root = Path(source_root)
        self._mock = mock
        self._procs: dict[str, subprocess.Popen] = {}
        self._mx_keys: dict[str, str] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── 生命周期 ──────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop, name="uzi-worker-monitor", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        for report_id in list(self._procs.keys()):
            try:
                self.cancel(report_id, message="Worker 服务关闭，任务已取消。")
            except Exception:  # noqa: BLE001
                logger.exception("停止时取消任务失败: %s", report_id)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    # ── 提交 ──────────────────────────────────────────────
    def submit_stage1(
        self,
        *,
        report_id: str,
        ticker: str,
        mx_api_key: str | None = None,
    ) -> WorkerJobState:
        if not _REPORT_ID_RE.match(str(report_id)):
            raise ValueError("report_id 必须是 1-10 位数字。")
        existing = self._store.get(report_id)
        if existing is not None and not existing.is_terminal:
            raise JobAlreadyExistsError(
                f"任务 {report_id} 仍在执行，不能重复提交 Stage 1。"
            )

        report_dir = self._store.report_dir(report_id)
        _ensure_report_dir(report_dir)

        if not self._mock:
            if not self._source_root.is_dir():
                raise SourceMissingError(
                    "UZI 源码未安装，请在 Worker 容器或独立 venv 中安装（UZI_SOURCE_ROOT）。"
                )
            self._copy_uzi_source(report_dir, report_id)

        state = WorkerJobState(report_id=report_id)
        state.started_at = state.updated_at
        state.mark_running(
            phase="stage1_running",
            progress=5,
            message="Worker 已接受 Stage 1，开始数据采集。",
        )
        if mx_api_key:
            register_secret(mx_api_key)
            with self._lock:
                self._mx_keys[report_id] = mx_api_key
        self._store.upsert(state)

        try:
            proc, log_path = self._spawn(
                report_id=report_id,
                stage="1",
                ticker=ticker,
                mx_api_key=mx_api_key,
            )
        except Exception:
            state.mark_failed(
                error_code="UZI_STAGE1_FAILED",
                error_message="Stage 1 进程启动失败。",
            )
            self._store.upsert(state)
            self._forget_mx_key(report_id)
            raise
        state.worker_pid = proc.pid
        with self._lock:
            self._procs[report_id] = proc
        self._store.upsert(state)
        logger.info("Stage 1 已启动: report_id=%s log=%s", report_id, log_path)
        return state

    def submit_stage2(self, *, report_id: str, ticker: str | None = None) -> WorkerJobState:
        state = self._store.get(report_id)
        if state is None:
            raise StageGuardError(
                "UZI_STAGE2_FAILED", f"任务 {report_id} 不存在，无法提交 Stage 2。"
            )
        if state.status == WorkerStatus.CANCELLED.value or state.status == WorkerStatus.FAILED.value:
            raise StageGuardError(
                "UZI_STAGE2_FAILED", f"任务 {report_id} 已结束，无法提交 Stage 2。"
            )
        if state.status in {"accepted", "running"} and state.phase == "stage2_running":
            raise StageGuardError(
                "UZI_STAGE2_FAILED", f"任务 {report_id} 的 Stage 2 正在执行。"
            )
        # 允许的入口：Stage 1 已成功（主服务完成 LLM 评审后提交 Stage 2）。
        if state.status != WorkerStatus.SUCCEEDED.value:
            raise StageGuardError(
                "UZI_STAGE2_FAILED", "Stage 1 尚未成功完成，拒绝提交 Stage 2。"
            )

        report_dir = self._store.report_dir(report_id)
        stage1_manifest = _load_json(report_dir / STAGE1_MANIFEST_REL)
        if not stage1_manifest or not stage1_manifest.get("success"):
            raise StageGuardError(
                "UZI_STAGE2_FAILED", "stage1-manifest.json 缺失或未成功。"
            )
        agent_analysis = _load_json(report_dir / AGENT_ANALYSIS_REL)
        if not agent_analysis:
            raise StageGuardError(
                "UZI_STAGE2_FAILED", "agent_analysis.json 不存在，拒绝提交 Stage 2。"
            )
        if agent_analysis.get("agent_reviewed") is not True:
            raise StageGuardError(
                "UZI_STAGE2_FAILED",
                "agent_reviewed 不为 true，拒绝生成完整报告。",
            )

        manifest_ticker = str(stage1_manifest.get("ticker_normalized") or "").strip()
        if ticker and manifest_ticker and ticker != manifest_ticker:
            raise StageGuardError(
                "UZI_STAGE2_FAILED",
                f"股票代码不一致（Stage 1: {manifest_ticker}，请求: {ticker}）。",
            )

        proc, log_path = self._spawn(
            report_id=report_id,
            stage="2",
            ticker=manifest_ticker or "",
        )
        state.mark_running(
            phase="stage2_running",
            progress=85,
            message="Stage 2 开始综合与报告渲染。",
        )
        state.worker_pid = proc.pid
        with self._lock:
            self._procs[report_id] = proc
        self._store.upsert(state)
        logger.info("Stage 2 已启动: report_id=%s log=%s", report_id, log_path)
        return state

    def cancel(self, report_id: str, *, message: str = "任务已被用户取消。") -> WorkerJobState:
        """取消任务：终止目标进程组，幂等操作（§11.5）。"""
        state = self._store.get(report_id)
        if state is None:
            raise LookupError(f"任务 {report_id} 不存在。")
        if state.is_terminal:
            return state

        self._terminate_process_group(report_id)
        with self._lock:
            self._procs.pop(report_id, None)
        self._forget_mx_key(report_id)
        state.mark_cancelled(message=message)
        self._store.upsert(state)
        return state

    def get(self, report_id: str) -> WorkerJobState | None:
        return self._store.get(report_id)

    def active_jobs(self) -> int:
        """当前活跃任务数（accepted/running）。"""
        return len(
            [
                state
                for state in self._store.all_states()
                if state.status in {"accepted", "running"}
            ]
        )

    # ── 子进程 ────────────────────────────────────────────
    def _spawn(
        self,
        *,
        report_id: str,
        stage: str,
        ticker: str,
        mx_api_key: str | None = None,
    ) -> tuple[subprocess.Popen, Path]:
        report_dir = self._store.report_dir(report_id)
        logs_dir = report_dir / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"stage{stage}.log"

        cmd = [
            sys.executable,
            "-m",
            "app.worker_child",
            "--report-id",
            report_id,
            "--stage",
            stage,
            "--ticker",
            ticker,
            "--report-root",
            str(self._report_root),
            "--source-root",
            str(self._source_root),
            "--mock" if self._mock else "--no-mock",
        ]
        env = dict(os.environ)
        env.update(get_stage_env())
        # 子进程通过 `-m app.worker_child` 启动：把 Worker 包根目录注入 PYTHONPATH。
        worker_root = str(Path(__file__).resolve().parents[1])
        existing_pythonpath = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            worker_root
            if not existing_pythonpath
            else worker_root + os.pathsep + existing_pythonpath
        )
        if mx_api_key:
            # 上游 UZI 代码读取的是 MX_APIKEY（review P2）；保留 UZI_MX_API_KEY
            # 作为兼容别名，避免日志/脱敏注册逻辑重复读取差异。
            env["MX_APIKEY"] = mx_api_key
            env["UZI_MX_API_KEY"] = mx_api_key

        log_handle = log_path.open("a", encoding="utf-8")
        proc = subprocess.Popen(  # noqa: S603 - 固定参数列表，禁止 shell
            cmd,
            cwd=str(report_dir / "work") if (report_dir / "work").exists() else str(report_dir),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # POSIX：独立进程组，取消时可 killpg
        )
        log_handle.close()
        return proc, log_path

    def _terminate_process_group(self, report_id: str) -> None:
        proc = self._procs.get(report_id)
        if proc is None:
            # 尝试从状态文件恢复 PID（Worker 重启后仍可取消）。
            state = self._store.get(report_id)
            if state is None or not state.worker_pid:
                return
            proc_pid = state.worker_pid
            # 无 Popen 句柄时回退到 killpg 检查（无法 wait 回收 zombie）。
            try:
                os.killpg(proc_pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                return
            deadline = time.monotonic() + _CANCEL_GRACE_SECONDS
            while time.monotonic() < deadline:
                try:
                    os.killpg(proc_pid, 0)
                except ProcessLookupError:
                    return
                except PermissionError:
                    return
                time.sleep(0.1)
            try:
                os.killpg(proc_pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            return

        # 有 Popen 句柄时用 wait() 回收子进程：SIGTERM 后 wait(timeout)，
        # 子进程立即退出则立即返回，避免固定 waiting 满 grace 秒（阻断项11）。
        proc_pid = proc.pid
        try:
            os.killpg(proc_pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return
        try:
            proc.wait(timeout=_CANCEL_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(proc_pid, signal.SIGKILL)
            proc.wait(timeout=2.0)
        except (ProcessLookupError, PermissionError, subprocess.TimeoutExpired):
            pass

    # ── 监控循环 ──────────────────────────────────────────
    def _monitor_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                report_ids = list(self._procs.keys())
            for report_id in report_ids:
                try:
                    self._poll(report_id)
                except Exception:  # noqa: BLE001
                    logger.exception("监控任务失败: %s", report_id)
            self._stop.wait(_MONITOR_POLL_SECONDS)

    def _poll(self, report_id: str) -> None:
        proc = self._procs.get(report_id)
        if proc is None:
            return
        exit_code = proc.poll()
        if exit_code is None:
            return

        with self._lock:
            self._procs.pop(report_id, None)
        self._forget_mx_key(report_id)

        state = self._store.get(report_id)
        if state is None or state.is_terminal:
            return

        report_dir = self._store.report_dir(report_id)
        if exit_code == 0:
            if state.phase in {"stage1_running", "stage1_done"}:
                self._finalize_stage1(report_id, state, report_dir)
            elif state.phase in {"stage2_running", "stage2_done"}:
                self._finalize_stage2(report_id, state, report_dir)
            else:
                # 子进程已结束但阶段未知，按阶段兜底：优先尝试 Stage 1 清单。
                if (report_dir / STAGE1_MANIFEST_REL).is_file():
                    self._finalize_stage1(report_id, state, report_dir)
                else:
                    self._finalize_stage2(report_id, state, report_dir)
        else:
            # 子进程失败：优先用子进程写入的错误码；否则按阶段兜底。
            if state.phase == "stage1_running":
                state.mark_failed(
                    error_code=state.error_code or ERROR_STAGE1_FAILED,
                    error_message=state.error_message
                    or "Stage 1 执行失败，详见 stage1.log。",
                )
            else:
                state.mark_failed(
                    error_code=state.error_code or ERROR_STAGE2_FAILED,
                    error_message=state.error_message
                    or "Stage 2 执行失败，详见 stage2.log。",
                )
            self._store.upsert(state)
            self._cleanup_after_failure(report_dir)

    def _finalize_stage1(
        self, report_id: str, state: WorkerJobState, report_dir: Path
    ) -> None:
        manifest = _load_json(report_dir / STAGE1_MANIFEST_REL)
        if not manifest or not manifest.get("success"):
            state.mark_failed(
                error_code=ERROR_STAGE1_FAILED,
                error_message="Stage 1 完成但 stage1-manifest.json 缺失或未成功。",
            )
            self._store.upsert(state)
            return
        state.mark_succeeded(
            phase="stage1_done",
            progress=45,
            message="Stage 1 完成：数据采集与机械评分已就绪，等待主服务深度评审。",
        )
        self._store.upsert(state)
        logger.info(
            "Stage 1 完成: report_id=%s ticker=%s",
            report_id,
            manifest.get("ticker_normalized"),
        )

    def _finalize_stage2(
        self, report_id: str, state: WorkerJobState, report_dir: Path
    ) -> None:
        ok, error_code, error_message = validate_and_finalize_artifacts(report_dir)
        if ok:
            state.mark_succeeded(
                phase="completed",
                progress=100,
                message="报告已生成并校验通过。",
            )
            state.artifacts_rel = ARTIFACTS_REL
            self._store.upsert(state)
            _cleanup_work_after_success(report_dir)
            logger.info("Stage 2 完成: report_id=%s", report_id)
        else:
            state.mark_failed(
                error_code=error_code or ERROR_ARTIFACT_INVALID,
                error_message=(
                    sanitize_error_message(error_message, report_root=self._report_root)
                    or "产物校验失败。"
                ),
            )
            self._store.upsert(state)
            _cleanup_after_failure(report_dir)

    def _forget_mx_key(self, report_id: str) -> None:
        """任务结束后注销 MX Key 脱敏登记（仅在内存中短暂存在）。"""
        with self._lock:
            key = self._mx_keys.pop(report_id, None)
        if key:
            unregister_secret(key)

    # ── 目录与源码隔离（§16.1, §12.3）─────────────────────
    def _copy_uzi_source(self, report_dir: Path, report_id: str) -> None:
        """每个任务一份源码副本，避免多个任务共享报告目录与缓存。"""
        work_dir = report_dir / "work"
        target = work_dir / "uzi"
        if target.exists():
            return
        shutil.copytree(self._source_root, target, ignore=_ignore_uzi_junk)
        logger.info("已复制 UZI 源码副本: report_id=%s", report_id)

    def _cleanup_after_failure(self, report_dir: Path) -> None:
        try:
            _safe_rmtree(report_dir / "work" / "uzi")
            _safe_rmtree(report_dir / "work" / ".cache")
            _safe_rmtree(report_dir / "work" / "reports")
        except Exception:  # noqa: BLE001
            logger.exception("失败清理不彻底")


def _ignore_uzi_junk(directory: str, names: list[str]) -> set[str]:
    ignored = {".git", "__pycache__", ".venv", "node_modules"}
    return {name for name in names if name in ignored}


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _ensure_report_dir(report_dir: Path) -> None:
    """创建任务目录；拒绝已存在但所有者不匹配的目录（§16.1）。"""
    if report_dir.exists():
        try:
            stat = report_dir.stat()
            if stat.st_uid != os.getuid():
                raise PermissionError(
                    f"报告目录所有者不匹配，拒绝使用: {report_dir.name}"
                )
        except FileNotFoundError:
            pass
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "work").mkdir(parents=True, exist_ok=True)
    (report_dir / "logs").mkdir(parents=True, exist_ok=True)


def _safe_rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _cleanup_work_after_success(report_dir: Path) -> None:
    """成功后删除源码副本、浏览器 Profile 与无用临时文件（§12.2）。"""
    _safe_rmtree(report_dir / "work" / "uzi")
    _safe_rmtree(report_dir / "work" / ".cache")
    _safe_rmtree(report_dir / "work" / "reports")
    for pattern in ("*.tmp", "*.log.tmp"):
        for path in report_dir.glob(pattern):
            try:
                path.unlink()
            except OSError:
                pass


def validate_and_finalize_artifacts(
    report_dir: Path,
) -> tuple[bool, str | None, str | None]:
    """Stage 2 产物校验 + 原子落位（文档 §12.4）。

    校验通过后把 ``artifacts.tmp`` 原子重命名为 ``artifacts/``，
    并生成 ``artifact-manifest.json``（大小 + SHA256）。
    """
    tmp_dir = report_dir / "artifacts.tmp"
    final_dir = report_dir / ARTIFACTS_REL

    if not tmp_dir.is_dir():
        return False, "UZI_ARTIFACT_INVALID", "artifacts.tmp 目录不存在。"

    # 1) full-report-standalone.html 存在、>10KB、UTF-8 可读。
    html_name = "full-report-standalone.html"
    index_path = tmp_dir / html_name
    if not index_path.is_file():
        return False, "UZI_ARTIFACT_INVALID", f"{html_name} 缺失。"
    if index_path.stat().st_size <= _MIN_HTML_BYTES:
        return False, "UZI_ARTIFACT_INVALID", f"{html_name} 小于 10KB，视为无效产物。"
    try:
        index_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return False, "UZI_ARTIFACT_INVALID", f"{html_name} 无法以 UTF-8 读取：{exc}"

    # 2) synthesis.json 存在且包含评分与 verdict（上游字段：overall_score/verdict_label）。
    synthesis_path = tmp_dir / "synthesis.json"
    synthesis = _load_json(synthesis_path)
    if synthesis is None:
        return False, "UZI_ARTIFACT_INVALID", "synthesis.json 缺失或格式错误。"
    score = synthesis.get("overall_score")
    verdict = str(synthesis.get("verdict_label") or "").strip()
    if not isinstance(score, (int, float)) or not verdict:
        return False, "UZI_ARTIFACT_INVALID", "synthesis.json 缺少 overall_score 或 verdict_label。"

    # 3) agent_analysis.json 存在且通过校验（agent_reviewed=true）。
    agent_path = report_dir / AGENT_ANALYSIS_REL
    agent = _load_json(agent_path)
    if agent is None:
        return False, "UZI_ARTIFACT_INVALID", "agent_analysis.json 缺失或格式错误。"
    if agent.get("agent_reviewed") is not True:
        return False, "UZI_ARTIFACT_INVALID", "agent_analysis.json 未通过结构校验。"

    # 4) 产物相对路径全部位于 artifacts.tmp 内（防越界）。
    artifact_entries: dict[str, dict[str, Any]] = {}
    for child in sorted(tmp_dir.iterdir()):
        if not child.is_file():
            continue
        if child.resolve().parent != tmp_dir.resolve():
            return False, "UZI_ARTIFACT_INVALID", "产物路径越界，已拒绝。"
        raw = child.read_bytes()
        artifact_entries[child.name] = {
            "file": child.name,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "mime": _mime_for_name(child.name),
        }

    manifest = {
        "schema_version": 1,
        "ticker": synthesis.get("ticker"),
        "overall_score": score,
        "verdict_label": verdict,
        "generated_at": synthesis.get("data_as_of"),
        "artifacts": sorted(
            artifact_entries.values(), key=lambda item: item["file"]
        ),
        "agent_analysis": {
            "file": AGENT_ANALYSIS_REL,
            "size": agent_path.stat().st_size,
            "sha256": hashlib.sha256(agent_path.read_bytes()).hexdigest(),
        },
    }
    (tmp_dir / "artifact-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 5) 原子移动：tmp → 正式目录。
    if final_dir.exists():
        _safe_rmtree(final_dir)
    try:
        os.replace(tmp_dir, final_dir)
    except OSError as exc:
        return False, "UZI_ARTIFACT_INVALID", f"产物原子移动失败：{exc}"
    return True, None, None


def _mime_for_name(name: str) -> str:
    lowered = name.lower()
    if lowered.endswith(".html") or lowered.endswith(".htm"):
        return "text/html; charset=utf-8"
    if lowered.endswith(".json"):
        return "application/json; charset=utf-8"
    if lowered.endswith(".txt"):
        return "text/plain; charset=utf-8"
    if lowered.endswith(".png"):
        return "image/png"
    return "application/octet-stream"