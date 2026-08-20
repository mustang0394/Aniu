"""账户级 API（多妙想 Key 多账户方案 §13 / §14）。"""

from __future__ import annotations

import json
from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.database import get_db
from app.schemas.accounts import (
    AccountLlmTestResult,
    AccountMxTestResult,
    AccountSkillListRead,
    TradingAccountCreate,
    TradingAccountRead,
    TradingAccountUpdate,
)
from app.schemas.aniu import (
    ChatMessageRead,
    ChatSessionCreate,
    ChatSessionMessagesPageRead,
    ChatSessionRead,
    ChatSessionUpdate,
    ChatStreamRequest,
    GlobalOverviewRead,
    PersistentSessionMessagesPageRead,
    PersistentSessionRead,
    RunDetailRead,
    RunSummaryPageRead,
    RunSummaryRead,
    RuntimeOverviewRead,
    ScheduleRead,
    ScheduleUpdate,
)
from app.services.account_service import account_service
from app.services.aniu_service import aniu_service
from app.services.chat_session_service import chat_session_service
from app.services.event_bus import event_bus

router = APIRouter(prefix="/api/aniu", tags=["aniu-accounts"])


def _require_account(db: Session, account_id: int):
    try:
        account_service.require_account(db, account_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# ── 账户 CRUD ────────────────────────────────────────────────────────────


@router.get("/accounts", response_model=list[TradingAccountRead])
def list_accounts(
    include_archived: bool = Query(default=False),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[TradingAccountRead]:
    return account_service.list_accounts_with_latest_run(db, include_archived=include_archived)


@router.post("/accounts", response_model=TradingAccountRead, status_code=201)
def create_account(
    payload: TradingAccountCreate,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> TradingAccountRead:
    try:
        account = account_service.create_account(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return account_service.to_read(db, account)


@router.get("/accounts/{account_id}", response_model=TradingAccountRead)
def get_account(
    account_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> TradingAccountRead:
    _require_account(db, account_id)
    return account_service.to_read(db, account_service.require_account(db, account_id))


@router.patch("/accounts/{account_id}", response_model=TradingAccountRead)
def update_account(
    account_id: int,
    payload: TradingAccountUpdate,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> TradingAccountRead:
    _require_account(db, account_id)
    account = account_service.update_account(db, account_id, payload)
    db.commit()
    return account_service.to_read(db, account)


@router.post("/accounts/{account_id}/archive", response_model=TradingAccountRead)
def archive_account(
    account_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> TradingAccountRead:
    _require_account(db, account_id)
    try:
        account = account_service.archive_account(db, account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return account_service.to_read(db, account)


@router.post("/accounts/{account_id}/restore", response_model=TradingAccountRead)
def restore_account(
    account_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> TradingAccountRead:
    _require_account(db, account_id)
    account = account_service.restore_account(db, account_id)
    db.commit()
    return account_service.to_read(db, account)


@router.post("/accounts/{account_id}/test-mx", response_model=AccountMxTestResult)
def test_account_mx(
    account_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> AccountMxTestResult:
    _require_account(db, account_id)
    return account_service.test_mx(db, account_id)


@router.post("/accounts/{account_id}/test-llm", response_model=AccountLlmTestResult)
def test_account_llm(
    account_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> AccountLlmTestResult:
    _require_account(db, account_id)
    return account_service.test_llm(db, account_id)


# ── 账户 Skills（§13.2） ──────────────────────────────────────────────────


@router.get("/accounts/{account_id}/skills", response_model=AccountSkillListRead)
def get_account_skills(
    account_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> AccountSkillListRead:
    _require_account(db, account_id)
    return account_service.get_account_skills(db, account_id)


@router.put("/accounts/{account_id}/skills", response_model=AccountSkillListRead)
def update_account_skills(
    account_id: int,
    payload: list[str],
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> AccountSkillListRead:
    _require_account(db, account_id)
    result = account_service.update_account_skills(db, account_id, payload)
    db.commit()
    return result


# ── 账户调度（§13.3） ────────────────────────────────────────────────────


@router.get("/accounts/{account_id}/schedule", response_model=list[ScheduleRead])
def get_account_schedule(
    account_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[ScheduleRead]:
    _require_account(db, account_id)
    return aniu_service.list_schedules(db, account_id=account_id)


@router.put("/accounts/{account_id}/schedule", response_model=list[ScheduleRead])
def update_account_schedule(
    account_id: int,
    payload: list[ScheduleUpdate],
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[ScheduleRead]:
    _require_account(db, account_id)
    return aniu_service.replace_schedules(db, payload, account_id=account_id)


# ── 账户运行（§13.4） ────────────────────────────────────────────────────


@router.post("/accounts/{account_id}/run", response_model=RunDetailRead)
def run_account_once(
    account_id: int,
    schedule_id: int | None = Query(default=None, ge=1),
    run_type: Literal["analysis", "trade"] | None = Query(default=None),
    _user: str = Depends(get_current_user),
) -> RunDetailRead:
    try:
        return aniu_service.execute_run(
            account_id=account_id,
            trigger_source="manual",
            schedule_id=schedule_id,
            manual_run_type=run_type,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/accounts/{account_id}/run-stream")
def run_account_stream(
    account_id: int,
    schedule_id: int | None = Query(default=None, ge=1),
    run_type: Literal["analysis", "trade"] | None = Query(default=None),
    _user: str = Depends(get_current_user),
) -> dict:
    try:
        run_id = aniu_service.start_run_async(
            account_id=account_id,
            trigger_source="manual",
            schedule_id=schedule_id,
            manual_run_type=run_type,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"run_id": run_id}


@router.get("/accounts/{account_id}/runs", response_model=list[RunSummaryRead])
def list_account_runs(
    account_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    run_date: date | None = Query(default=None, alias="date"),
    status: str | None = Query(default=None),
    before_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[RunSummaryRead]:
    _require_account(db, account_id)
    return aniu_service.list_runs(
        db,
        limit=limit,
        run_date=run_date,
        status=status,
        before_id=before_id,
        account_id=account_id,
    )


@router.get("/accounts/{account_id}/runs-feed", response_model=RunSummaryPageRead)
def list_account_runs_feed(
    account_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    run_date: date | None = Query(default=None, alias="date"),
    status: str | None = Query(default=None),
    before_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> RunSummaryPageRead:
    _require_account(db, account_id)
    return aniu_service.list_runs_page(
        db,
        limit=limit,
        run_date=run_date,
        status=status,
        before_id=before_id,
        account_id=account_id,
    )


@router.get("/accounts/{account_id}/runs/{run_id}/events")
def account_run_events(
    account_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> StreamingResponse:
    run = aniu_service.get_run(db, run_id, account_id=account_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在。")

    def _generator():
        try:
            for event in event_bus.stream(run_id):
                event_type = str(event.get("type") or "message")
                data = json.dumps(event, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {data}\n\n"
        except Exception as exc:  # noqa: BLE001
            err = json.dumps({"type": "failed", "message": str(exc)}, ensure_ascii=False)
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


@router.get("/accounts/{account_id}/runs/{run_id}", response_model=RunDetailRead)
def get_account_run(
    account_id: int,
    run_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> RunDetailRead:
    run = aniu_service.get_run(db, run_id, account_id=account_id)
    if run is None:
        raise HTTPException(status_code=404, detail="运行记录不存在。")
    return run


@router.get(
    "/accounts/{account_id}/runs/{run_id}/raw-tool-previews/{preview_index}",
)
def get_account_run_raw_tool_preview(
    account_id: int,
    run_id: int,
    preview_index: int = Path(ge=0),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
):
    try:
        return aniu_service.get_run_raw_tool_preview(
            db, run_id, preview_index, account_id=account_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/accounts/{account_id}/runs/{run_id}", status_code=204)
def delete_account_run(
    account_id: int,
    run_id: int,
    force: bool = Query(default=False),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> None:
    try:
        aniu_service.delete_run(db, run_id, force=force, account_id=account_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ── 总览（§13.5） ────────────────────────────────────────────────────────


@router.get("/accounts/{account_id}/runtime-overview", response_model=RuntimeOverviewRead)
def get_account_runtime_overview(
    account_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> RuntimeOverviewRead:
    _require_account(db, account_id)
    try:
        return aniu_service.get_runtime_overview(db, account_id=account_id)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/accounts/{account_id}/overview")
def get_account_overview(
    account_id: int,
    force_refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    _require_account(db, account_id)
    try:
        return aniu_service.get_account_overview(
            account_id, force_refresh=force_refresh
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/accounts/{account_id}/overview/debug")
def get_account_overview_debug(
    account_id: int,
    force_refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> dict:
    _require_account(db, account_id)
    try:
        return aniu_service.get_account_overview(
            account_id, include_raw=True, force_refresh=force_refresh
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/overview", response_model=GlobalOverviewRead)
def get_global_overview(
    force_refresh: bool = Query(default=False),
    _user: str = Depends(get_current_user),
) -> GlobalOverviewRead:
    return aniu_service.get_global_overview(force_refresh=force_refresh)


# ── 账户聊天与会话（§14） ────────────────────────────────────────────────


@router.get("/accounts/{account_id}/chat/sessions", response_model=list[ChatSessionRead])
def list_account_chat_sessions(
    account_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> list[ChatSessionRead]:
    _require_account(db, account_id)
    return chat_session_service.list_sessions(db, account_id=account_id)


@router.post("/accounts/{account_id}/chat/sessions", response_model=ChatSessionRead)
def create_account_chat_session(
    account_id: int,
    payload: ChatSessionCreate | None = None,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ChatSessionRead:
    _require_account(db, account_id)
    title = payload.title if payload else None
    result = chat_session_service.create_session(
        db, title=title, account_id=account_id
    )
    db.commit()
    return result


@router.patch(
    "/accounts/{account_id}/chat/sessions/{session_id}",
    response_model=ChatSessionRead,
)
def rename_account_chat_session(
    account_id: int,
    session_id: int,
    payload: ChatSessionUpdate,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ChatSessionRead:
    _require_account(db, account_id)
    try:
        result = chat_session_service.rename_session(
            db, session_id, title=payload.title, account_id=account_id
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return result


@router.delete(
    "/accounts/{account_id}/chat/sessions/{session_id}", status_code=204
)
def delete_account_chat_session(
    account_id: int,
    session_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> None:
    _require_account(db, account_id)
    try:
        chat_session_service.delete_session(db, session_id, account_id=account_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()


@router.get(
    "/accounts/{account_id}/chat/sessions/{session_id}/messages",
    response_model=ChatSessionMessagesPageRead,
)
def list_account_chat_messages(
    account_id: int,
    session_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> ChatSessionMessagesPageRead:
    _require_account(db, account_id)
    try:
        session, messages, next_before_id, has_more = chat_session_service.list_messages(
            db,
            session_id,
            limit=limit,
            before_id=before_id,
            account_id=account_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "session": session.model_dump(mode="json"),
        "messages": [m.model_dump(mode="json") for m in messages],
        "next_before_id": next_before_id,
        "has_more": has_more,
    }


@router.get(
    "/accounts/{account_id}/persistent-session",
    response_model=PersistentSessionRead,
)
def get_account_persistent_session(
    account_id: int,
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> PersistentSessionRead:
    _require_account(db, account_id)
    return aniu_service.get_persistent_session(db, account_id=account_id)


@router.get(
    "/accounts/{account_id}/persistent-session/messages",
    response_model=PersistentSessionMessagesPageRead,
)
def list_account_persistent_session_messages(
    account_id: int,
    limit: int = Query(default=50, ge=1, le=100),
    before_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    _user: str = Depends(get_current_user),
) -> PersistentSessionMessagesPageRead:
    _require_account(db, account_id)
    session, messages, next_before_id, has_more = (
        aniu_service.list_persistent_session_messages(
            db,
            limit=limit,
            before_id=before_id,
            account_id=account_id,
        )
    )
    return {
        "session": session.model_dump(mode="json"),
        "messages": [m.model_dump(mode="json") for m in messages],
        "next_before_id": next_before_id,
        "has_more": has_more,
    }


@router.post("/accounts/{account_id}/chat/stream")
def account_chat_session_stream(
    account_id: int,
    payload: ChatStreamRequest,
    _user: str = Depends(get_current_user),
) -> StreamingResponse:
    payload.account_id = account_id
    try:
        event_iter = chat_session_service.stream_chat(payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    def _generator():
        try:
            for event in event_iter:
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
