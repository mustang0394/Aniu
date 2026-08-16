"""UZI 深度报告公共 API（文档 §10）。

所有接口沿用 ``/api/aniu`` 前缀和现有 JWT 鉴权（``get_current_user``）。
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.rate_limit import get_client_ip
from app.db.database import get_db
from app.schemas.uzi import (
    UziCancelResponse,
    UziReportCreateRequest,
    UziReportCreateResponse,
    UziReportDetailRead,
    UziReportListResponse,
    UziStatusRead,
)
from app.services.uzi_report_service import (
    ERROR_DISABLED,
    ERROR_INVALID_TICKER,
    ERROR_LLM_NOT_CONFIGURED,
    ERROR_QUEUE_FULL,
    ERROR_WORKER_UNAVAILABLE,
    uzi_report_service,
)

router = APIRouter(prefix="/api/aniu/uzi", tags=["uzi"])

# 错误码 → HTTP 状态映射（稳定错误码，§17.3）。
_ERROR_STATUS_MAP = {
    ERROR_DISABLED: 503,
    ERROR_WORKER_UNAVAILABLE: 503,
    ERROR_QUEUE_FULL: 409,
    ERROR_INVALID_TICKER: 422,
    ERROR_LLM_NOT_CONFIGURED: 422,
    "UZI_RATE_LIMITED": 429,
}


def _raise_http(exc: Exception) -> None:
    if isinstance(exc, ValueError) and len(exc.args) >= 2:
        error_code = str(exc.args[0])
        message = str(exc.args[1])
        status_code = _ERROR_STATUS_MAP.get(error_code, 400)
        raise HTTPException(status_code=status_code, detail=message) from exc
    if isinstance(exc, RuntimeError) and len(exc.args) >= 2:
        error_code = str(exc.args[0])
        message = str(exc.args[1])
        status_code = _ERROR_STATUS_MAP.get(error_code, 409)
        raise HTTPException(status_code=status_code, detail=message) from exc
    if isinstance(exc, LookupError):
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    raise HTTPException(status_code=500, detail="服务器内部错误。") from exc


@router.get("/status", response_model=UziStatusRead)
def get_uzi_status(
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> UziStatusRead:
    from app.core.config import get_settings

    settings = get_settings()
    health = uzi_report_service._worker_health()
    active = uzi_report_service._count_active(db)
    queued = max(0, active - settings.uzi_max_active)
    reason = None
    if not settings.uzi_enabled:
        reason = "UZI 模块未启用。"
    elif health is None:
        reason = "UZI Worker 未配置或不可用。"
    return UziStatusRead(
        enabled=settings.uzi_enabled,
        worker_available=health is not None,
        worker_version=(
            str(health.get("uzi_commit") or health.get("worker_version") or "")
            if health
            else None
        ),
        active_jobs=active,
        queued_jobs=queued,
        max_queued=settings.uzi_max_queued,
        reason=reason,
    )


@router.post("/reports", response_model=UziReportCreateResponse, status_code=202)
def create_report(
    payload: UziReportCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> UziReportCreateResponse:
    try:
        job, reused = uzi_report_service.create_report(
            db, ticker=payload.ticker, client_ip=get_client_ip(request)
        )
    except (ValueError, RuntimeError) as exc:
        _raise_http(exc)
    summary = uzi_report_service._to_summary(job)
    return UziReportCreateResponse(report=summary, reused=reused)


@router.get("/reports", response_model=UziReportListResponse)
def list_reports(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ticker: str | None = Query(default=None),
    status: str | None = Query(default=None),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> UziReportListResponse:
    try:
        page = uzi_report_service.list_reports(
            db,
            limit=limit,
            offset=offset,
            ticker=ticker,
            status=status,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UziReportListResponse(**page)


@router.get("/reports/{report_id}", response_model=UziReportDetailRead)
def get_report(
    report_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> UziReportDetailRead:
    detail = uzi_report_service.get_report(db, report_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="报告任务不存在。")
    return detail


@router.get("/reports/{report_id}/events")
def report_events(
    report_id: int = Path(ge=1),
    _user: str = Depends(get_current_user),
) -> StreamingResponse:
    def _generator():
        try:
            for event in uzi_report_service.stream_events(report_id):
                event_type = str(event.get("type") or "message")
                data = json.dumps(event, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {data}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = json.dumps(
                {"type": "failed", "message": str(exc)}, ensure_ascii=False
            )
            yield f"event: failed\ndata: {err}\n\n"

    headers = {
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    }
    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post("/reports/{report_id}/cancel", response_model=UziCancelResponse)
def cancel_report(
    report_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> UziCancelResponse:
    try:
        result = uzi_report_service.cancel_report(db, report_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return UziCancelResponse(**result)


@router.delete("/reports/{report_id}", status_code=204)
def delete_report(
    report_id: int = Path(ge=1),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> None:
    try:
        uzi_report_service.delete_report(db, report_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/reports/{report_id}/artifacts/{artifact_key}")
def get_artifact(
    report_id: int = Path(ge=1),
    artifact_key: str = Path(...),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> Response:
    try:
        path, mime, download_name = uzi_report_service.get_artifact(
            db, report_id, artifact_key
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if path is None:
        raise HTTPException(status_code=404, detail="产物不存在。")
    headers = {
        "X-Content-Type-Options": "nosniff",
    }
    if download_name:
        return FileResponse(
            str(path),
            media_type=mime or "application/octet-stream",
            filename=download_name,
            headers=headers,
        )
    return FileResponse(
        str(path),
        media_type=mime or "text/html; charset=utf-8",
        headers=headers,
    )
