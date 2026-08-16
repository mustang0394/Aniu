"""aniu-uzi-worker FastAPI 应用。

只监听 Docker 内部网络（文档 §4 / §11），所有 ``/internal/*`` 请求必须
携带 ``X-Aniu-Uzi-Token`` 并与 ``UZI_WORKER_TOKEN`` 一致。

健康检查 ``GET /internal/health`` 按部署需要豁免 Token 校验。
"""
from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import get_worker_config
from app.models import WorkerJobState
from app.runner import (
    JobAlreadyExistsError,
    JobRunner,
    SourceMissingError,
    StageGuardError,
)
from app.sanitize import (
    install_sanitizing_filter,
    register_secret,
    sanitize_error_message,
)
from app.state_store import StateStore

logger = logging.getLogger(__name__)

_AUTH_HEADER = "X-Aniu-Uzi-Token"
_REPORT_ID_RE = re.compile(r"^\d{1,10}$")
_TICKER_MAX_LENGTH = 64
# 拒绝控制字符与路径/命令分隔类字符（输入只作函数参数，绝不进 shell）。
_TICKER_INVALID_RE = re.compile(r"[\x00-\x1f\x7f/\\;\`$|<>&]")


def _load_uzi_commit() -> str:
    """从 uzi-source.lock 读取固定上游版本。"""
    lock_path = Path(__file__).resolve().parent.parent / "uzi-source.lock"
    try:
        payload = json.loads(lock_path.read_text(encoding="utf-8"))
        return str(payload.get("commit") or "")[:7]
    except Exception:  # noqa: BLE001
        return "unknown"


def _probe_chromium() -> bool:
    """启动时探测一次 Chromium 可用性（不启动浏览器）。"""
    import os

    try:
        import playwright  # noqa: F401 - 仅验证包可导入
    except Exception:  # noqa: BLE001 - playwright 未安装时视为不可用
        return False

    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or (
        Path.home() / ".cache" / "ms-playwright"
    )
    path = Path(browsers_path)
    if path.is_dir():
        for child in path.iterdir():
            if child.name.startswith("chromium"):
                return True
    return False


class Stage1Request(BaseModel):
    ticker: str = Field(min_length=1, max_length=_TICKER_MAX_LENGTH)
    report_rel_dir: str = Field(min_length=1, max_length=16)
    mx_api_key: str | None = None


class Stage2Request(BaseModel):
    """Stage 2 提交契约（§11.4）：ticker 可选，用于与 Stage 1 清单比对。"""

    ticker: str | None = Field(default=None, max_length=_TICKER_MAX_LENGTH)


def _validate_ticker(ticker: str) -> str:
    cleaned = str(ticker or "").strip()
    if not cleaned:
        raise HTTPException(status_code=422, detail="ticker 不能为空。")
    if len(cleaned) > _TICKER_MAX_LENGTH:
        raise HTTPException(
            status_code=422, detail=f"ticker 长度不能超过 {_TICKER_MAX_LENGTH}。"
        )
    if _TICKER_INVALID_RE.search(cleaned):
        raise HTTPException(
            status_code=422, detail="ticker 包含不允许的字符。"
        )
    return cleaned


def _require_token(request: Request) -> None:
    config = get_worker_config()
    if not config.token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未配置 UZI_WORKER_TOKEN。",
        )
    provided = request.headers.get(_AUTH_HEADER)
    if provided != config.token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="内部令牌无效。",
        )


def _state_response(state: WorkerJobState) -> dict:
    payload = state.to_api_dict()
    if state.error_message:
        payload["error_message"] = sanitize_error_message(
            state.error_message, report_root=get_worker_config().report_root
        )
    return payload


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    install_sanitizing_filter()
    config = get_worker_config()
    if config.token:
        register_secret(config.token)
    logger.info(
        "UZI Worker 启动: source_root=%s mock=%s",
        config.source_root,
        config.mock,
    )
    app.state.store = StateStore(config.report_root)
    app.state.runner = JobRunner(
        store=app.state.store,
        report_root=config.report_root,
        source_root=config.source_root,
        mock=config.mock,
    )
    app.state.runner.start()
    try:
        yield
    finally:
        app.state.runner.stop()
        logger.info("UZI Worker 已停止")


def create_app() -> FastAPI:
    config = get_worker_config()
    app = FastAPI(title="aniu-uzi-worker", lifespan=app_lifespan)

    app.state.uzi_commit = _load_uzi_commit()

    # ── 健康检查（豁免 Token，§11.1）───────────────────────
    @app.get("/internal/health")
    def health() -> dict:
        store: StateStore = getattr(app.state, "store", None)
        runner: JobRunner = getattr(app.state, "runner", None)
        active = runner.active_jobs() if runner else 0
        states = store.all_states() if store else []
        queued = len([s for s in states if s.status == "accepted"])
        # readiness：区分 liveness 与就绪度（review 问题9）。
        # token 未配置/源码不存在/chromium 不可用时 ready=false 并带原因。
        token_ok = bool(config.token)
        source_ok = config.mock or config.source_root.is_dir()
        chromium_ok = config.mock or _probe_chromium()
        reasons: list[str] = []
        if not token_ok:
            reasons.append("UZI_WORKER_TOKEN 未配置")
        if not source_ok:
            reasons.append(f"UZI_SOURCE_ROOT 不存在: {config.source_root}")
        if not chromium_ok:
            reasons.append("Chromium 不可用")
        return {
            "status": "ok",
            "ready": token_ok and source_ok and chromium_ok,
            "reason": "; ".join(reasons) if reasons else None,
            "worker_version": app.state.uzi_commit,
            "uzi_commit": app.state.uzi_commit,
            "chromium_available": chromium_ok,
            "mock": bool(config.mock),
            "active_jobs": active,
            "queued_jobs": queued,
            "token_configured": token_ok,
        }

    # ── 受保护路由：全部要求 Token ──────────────────────────
    @app.post("/internal/jobs/{report_id}/stage1")
    def submit_stage1(
        report_id: str,
        payload: Stage1Request,
        request: Request,
    ) -> JSONResponse:
        _require_token(request)
        if not _REPORT_ID_RE.match(report_id):
            raise HTTPException(status_code=422, detail="report_id 必须是 1-10 位数字。")
        # 文档 §11.2：必须验证 report_rel_dir 与 report_id 一致，不允许任意目录。
        if payload.report_rel_dir != report_id:
            raise HTTPException(
                status_code=400,
                detail="report_rel_dir 与 report_id 不一致。",
            )
        ticker = _validate_ticker(payload.ticker)
        try:
            state = app.state.runner.submit_stage1(
                report_id=report_id,
                ticker=ticker,
                mx_api_key=payload.mx_api_key,
            )
        except JobAlreadyExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except SourceMissingError as exc:
            raise HTTPException(
                status_code=503,
                detail=sanitize_error_message(
                    str(exc), report_root=config.report_root
                ),
            ) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"job": _state_response(state)},
        )

    @app.get("/internal/jobs/{report_id}")
    def get_job(report_id: str, request: Request) -> dict:
        _require_token(request)
        if not _REPORT_ID_RE.match(report_id):
            raise HTTPException(status_code=422, detail="report_id 必须是 1-10 位数字。")
        state = app.state.runner.get(report_id)
        if state is None:
            raise HTTPException(status_code=404, detail="任务不存在。")
        return {"job": _state_response(state)}

    @app.post("/internal/jobs/{report_id}/stage2")
    def submit_stage2(
        report_id: str,
        request: Request,
        payload: Stage2Request | None = None,
    ) -> JSONResponse:
        _require_token(request)
        if not _REPORT_ID_RE.match(report_id):
            raise HTTPException(status_code=422, detail="report_id 必须是 1-10 位数字。")
        try:
            state = app.state.runner.submit_stage2(
                report_id=report_id,
                ticker=(payload.ticker if payload else None) or None,
            )
        except StageGuardError as exc:
            raise HTTPException(
                status_code=409,
                detail=sanitize_error_message(
                    f"{exc.error_code}: {exc.message}",
                    report_root=config.report_root,
                ),
            ) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"job": _state_response(state)},
        )

    @app.post("/internal/jobs/{report_id}/cancel")
    def cancel_job(report_id: str, request: Request) -> dict:
        _require_token(request)
        if not _REPORT_ID_RE.match(report_id):
            raise HTTPException(status_code=422, detail="report_id 必须是 1-10 位数字。")
        try:
            state = app.state.runner.cancel(report_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"job": _state_response(state)}

    return app


app = create_app()