from __future__ import annotations

import inspect
import json
import logging
import queue
import secrets
import time
import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock, Thread
from types import SimpleNamespace
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.core.auth import create_access_token
from app.core.config import get_settings
from app.core.constants import DEFAULT_APP_DISPLAY_NAME, DEFAULT_SYSTEM_PROMPT
from app.db.database import session_scope
from app.db.models import (
    AppSettings,
    ChatMessageRecord,
    ChatSession,
    StrategyRun,
    StrategySchedule,
    TradingAccount,
    TradeOrder,
)
from app.schemas.aniu import AppSettingsUpdate, ChatRequest, ScheduleUpdate
from app.schemas.aniu import ChatMessageRead, PersistentSessionRead
from app.schemas.accounts import TradingAccountRead
from app.services.account_context import (
    AUTOMATION_SESSION_SLUG_PREFIX,
    AccountRunContext,
    build_account_run_context,
    get_or_create_automation_session,
    parse_disabled_skill_ids,
)
from app.services.account_service import account_service
from app.skills.providers import build_skill_context
from app.services.event_bus import event_bus, make_emitter
from app.services.llm_service import (
    LLMFreshnessError,
    LLMMutationSafetyError,
    LLMStreamCancelled,
    llm_service,
)
from app.services.token_estimator import estimate_messages_tokens, estimate_text_tokens
from app.services.trading_calendar_service import trading_calendar_service
from skills.mx_core.client import MXClient
from skills.mx_core.execution import mx_execution_service
from skills.mx_core.markets import normalize_allowed_markets


logger = logging.getLogger(__name__)

RAW_TOOL_PREVIEW_MAX_CHARS = 6000

SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
ANALYSIS_TASK_NAMES = {"盘前分析", "午间复盘", "收盘分析"}
SCHEDULE_RETRY_DELAY = timedelta(minutes=5)
SCHEDULE_MAX_RETRIES = 3
ACCOUNT_PREFETCH_TOOL_NAMES = (
    "mx_get_balance",
    "mx_get_positions",
    "mx_get_orders",
)
ACCOUNT_OVERVIEW_CACHE_MAX_WORKERS = 3
AUTOMATION_SESSION_TITLE = "自动化交易会话"
AUTOMATION_DEFAULT_CONTEXT_WINDOW_TOKENS = 128000
AUTOMATION_DEFAULT_RECENT_MESSAGE_LIMIT = 24
AUTOMATION_DEFAULT_IDLE_SUMMARY_HOURS = 12
AUTOMATION_COMPACTION_TRIGGER_RATIO = 0.85
SCHEDULE_LEASE_SECONDS = 600


@dataclass
class PersistentRunSessionContext:
    session_id: int
    prompt_message_id: int
    response_message_id: int | None
    summary_revision: int | None
    context_tokens_estimate: int | None
    messages: list[dict[str, Any]]


def _owns_schedule_lease(schedule: Any, lease_token: str | None) -> bool:
    """lease 所有权校验：token 匹配才算持有者；无条件清理仅限无租约状态。"""
    current_token = str(getattr(schedule, "lease_token", None) or "")
    if not current_token:
        # 无租约：手动运行等场景可正常更新调度状态。
        return True
    if not lease_token:
        return False
    return current_token == str(lease_token)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_shanghai() -> datetime:
    return now_utc().astimezone(SHANGHAI_TZ)


def _assume_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_percent(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100.0


def _scaled_decimal(value: Any, decimal_places: Any) -> float | None:
    numeric = _parse_float(value)
    if numeric is None:
        return None

    decimals = _parse_float(decimal_places)
    scale = int(decimals) if decimals is not None else 0
    if scale <= 0:
        return numeric
    return numeric / (10**scale)


def _market_suffix(value: Any) -> str:
    mapping = {
        0: "SZ",
        1: "SH",
    }
    numeric = _parse_float(value)
    if numeric is None:
        return ""
    return mapping.get(int(numeric), "")


def _format_open_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text or None


def _format_timestamp(value: Any) -> str | None:
    numeric = _parse_float(value)
    if numeric is None:
        return None
    if numeric > 10_000_000_000:
        numeric = numeric / 1000
    try:
        return datetime.fromtimestamp(numeric, tz=SHANGHAI_TZ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (OverflowError, OSError, ValueError):
        return None


def _coalesce(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _order_status_text(
    value: Any,
    *,
    filled_quantity: Any = None,
    order_quantity: Any = None,
    db_status: Any = None,
) -> str:
    filled = int(_parse_float(filled_quantity) or 0)
    total = int(_parse_float(order_quantity) or 0)
    if total > 0:
        if filled >= total and filled > 0:
            return "已成交"
        if 0 < filled < total:
            return "部分成交"

    mapping = {
        "0": "未知",
        "1": "已报",
        "2": "已报",
        "3": "已撤单",
        "4": "已成交",
        "8": "未成交",
        "9": "已撤单",
        "100": "处理中",
        "200": "已完成",
        "206": "已撤单",
    }
    text = str(value or "").strip()
    if text == "" and db_status is not None:
        text = str(db_status).strip()
    return mapping.get(text, text or "未知")


class AniuService:
    def __init__(self) -> None:
        self._account_run_locks: dict[int, Lock] = {}
        self._account_run_locks_guard = Lock()
        self._account_cache_lock = Lock()
        self._account_overview_cache: dict[int, dict[str, Any]] = {}
        self._account_overview_cache_expires_at: dict[int, datetime] = {}

    # ── 账户解析助手（旧接口兼容：单账户自动映射，多账户必须显式） ────────

    def _resolve_account_id(
        self, db: Session, account_id: int | None = None
    ) -> int:
        """解析目标账户 ID。

        - 显式提供：直接使用并校验存在。
        - 未提供：仅当一个未归档账户存在时自动映射；否则拒绝。
        """
        if account_id is not None:
            account = db.get(TradingAccount, account_id)
            if account is None:
                raise RuntimeError("交易账户不存在。")
            return account_id
        accounts = account_service.list_accounts(db, include_archived=False)
        if len(accounts) == 1:
            return accounts[0].id
        if not accounts:
            raise RuntimeError("不存在可用交易账户，请先在账户管理中创建。")
        raise RuntimeError(
            "存在多个交易账户，必须显式指定账户执行该操作。"
        )

    def _resolve_account(
        self, db: Session, account_id: int | None = None
    ) -> TradingAccount:
        resolved = self._resolve_account_id(db, account_id)
        return account_service.require_account(db, resolved)

    def _get_account_run_lock(self, account_id: int) -> Lock:
        with self._account_run_locks_guard:
            return self._account_run_locks.setdefault(account_id, Lock())

    def _resolve_run_type(self, schedule: StrategySchedule | None) -> str:
        if schedule is None:
            return "analysis"

        run_type = str(schedule.run_type or "").strip()
        if run_type in {"analysis", "trade"}:
            return run_type

        name = str(schedule.name or "").strip()
        if name.startswith("上午运行") or name.startswith("下午运行"):
            return "trade"
        return "analysis"

    def _run_agent_supports_emit(self, run_agent: Any) -> bool:
        try:
            signature = inspect.signature(run_agent)
        except (TypeError, ValueError):
            return True

        for parameter in signature.parameters.values():
            if parameter.kind is inspect.Parameter.VAR_KEYWORD:
                return True
            if parameter.name == "emit":
                return True
        return False

    def _infer_run_type(self, run: StrategyRun) -> str:
        schedule_name = str(run.schedule_name or "").strip()
        if schedule_name in ANALYSIS_TASK_NAMES:
            return "analysis"
        if schedule_name.startswith("上午运行") or schedule_name.startswith("下午运行"):
            return "trade"

        if run.trade_orders:
            return "trade"

        executed_actions = run.executed_actions if isinstance(run.executed_actions, list) else []
        trade_actions = {"BUY", "SELL", "CANCEL"}
        if any(str(item.get("action") or "").upper() in trade_actions for item in executed_actions if isinstance(item, dict)):
            return "trade"

        tool_calls = self._get_run_tool_calls(run)
        trade_tool_names = {"mx_moni_trade", "mx_moni_cancel"}
        if any(str(item.get("name") or "") in trade_tool_names for item in tool_calls):
            return "trade"

        stored_run_type = str(run.run_type or "").strip()
        if stored_run_type in {"trade", "analysis"}:
            return stored_run_type

        return "analysis"

    def authenticate_login(self, password: str) -> dict[str, Any]:
        settings = get_settings()
        expected_password = settings.app_login_password

        if not expected_password:
            raise RuntimeError("未配置登录密码，请先设置 APP_LOGIN_PASSWORD。")

        if not secrets.compare_digest(password, expected_password):
            raise RuntimeError("密码错误。")

        token = create_access_token("single-user")
        return {
            "authenticated": True,
            "token": token,
        }

    def get_or_create_settings(self, db: Session) -> AppSettings:
        instance = db.scalar(select(AppSettings).limit(1))
        if instance is None:
            env = get_settings()
            instance = AppSettings(
                app_display_name=DEFAULT_APP_DISPLAY_NAME,
                provider_name="openai-compatible",
                mx_api_key=env.mx_apikey,
                llm_base_url=env.openai_base_url,
                llm_api_key=env.openai_api_key,
                llm_model=env.openai_model,
                system_prompt=DEFAULT_SYSTEM_PROMPT,
            )
            db.add(instance)
            db.commit()
            db.refresh(instance)
        return instance

    def list_schedules(
        self, db: Session, account_id: int | None = None
    ) -> list[StrategySchedule]:
        resolved_account_id = self._resolve_account_id(db, account_id)
        stmt = (
            select(StrategySchedule)
            .where(StrategySchedule.trading_account_id == resolved_account_id)
            .order_by(StrategySchedule.id.asc())
        )
        schedules = list(db.scalars(stmt).all())
        mutated = False
        for schedule in schedules:
            if not schedule.name:
                schedule.name = "默认任务"
                mutated = True
            if str(schedule.run_type or "").strip() not in {"analysis", "trade"}:
                schedule.run_type = self._resolve_run_type(schedule)
                mutated = True
            if not schedule.cron_expression:
                schedule.cron_expression = "*/30 * * * *"
                mutated = True
            if not schedule.task_prompt:
                schedule.task_prompt = "请根据当前市场和持仓情况生成交易决策。"
                mutated = True
            if not schedule.timeout_seconds or schedule.timeout_seconds <= 0:
                schedule.timeout_seconds = 1800
                mutated = True
            if schedule.retry_count < 0:
                schedule.retry_count = 0
                mutated = True
            if schedule.enabled and schedule.next_run_at is None:
                schedule.next_run_at = self._compute_next_run_at(
                    schedule.cron_expression
                )
                mutated = True
        if mutated:
            db.commit()
            for schedule in schedules:
                db.refresh(schedule)
        if not schedules:
            instance = StrategySchedule(
                name="默认任务",
                run_type="analysis",
                trading_account_id=resolved_account_id,
                cron_expression="*/30 * * * *",
                task_prompt="请根据当前市场和持仓情况生成交易决策。",
                timeout_seconds=1800,
                enabled=False,
            )
            db.add(instance)
            db.commit()
            db.refresh(instance)
            schedules = [instance]
        for schedule in schedules:
            schedule.retry_count = max(int(schedule.retry_count or 0), 0)
            schedule.last_run_at = _assume_utc(schedule.last_run_at)
            schedule.next_run_at = _assume_utc(schedule.next_run_at)
            schedule.retry_after_at = _assume_utc(schedule.retry_after_at)
            schedule.lease_until = _assume_utc(schedule.lease_until)
            schedule.created_at = _assume_utc(schedule.created_at)
            schedule.updated_at = _assume_utc(schedule.updated_at)
            if schedule.trading_account_id:
                schedule_account = db.get(
                    TradingAccount, schedule.trading_account_id
                )
                schedule.trading_account_name = (
                    schedule_account.name if schedule_account is not None else None
                )
            else:
                schedule.trading_account_name = None
        return schedules

    def update_settings(self, db: Session, payload: AppSettingsUpdate) -> AppSettings:
        instance = self.get_or_create_settings(db)
        sensitive_fields = {
            "mx_api_key",
            "uzi_mx_api_key",
            "llm_api_key",
            "tg_bot_token",
            "tg_chat_id",
        }
        changed_fields: list[str] = []
        orm_fields = (
            payload.to_orm_fields()
            if hasattr(payload, "to_orm_fields")
            else payload.model_dump()
        )
        for field, value in orm_fields.items():
            if field in sensitive_fields:
                if isinstance(value, str) and "****" in value:
                    continue
            old_value = getattr(instance, field, None)
            if old_value != value:
                changed_fields.append(field)
            setattr(instance, field, value)
        db.add(instance)
        db.commit()
        db.refresh(instance)
        instance.created_at = _assume_utc(instance.created_at)
        instance.updated_at = _assume_utc(instance.updated_at)
        logger.info("settings updated: changed_fields=%s", changed_fields)
        return instance

    def replace_schedules(
        self,
        db: Session,
        payloads: list[ScheduleUpdate],
        account_id: int | None = None,
    ) -> list[StrategySchedule]:
        resolved_account_id = self._resolve_account_id(db, account_id)
        existing = {
            item.id: item
            for item in self.list_schedules(db, account_id=resolved_account_id)
        }
        keep_ids: set[int] = set()

        for payload in payloads:
            data = payload.model_dump()
            schedule_id = data.pop("id", None)
            if schedule_id is not None and schedule_id in existing:
                instance = existing[schedule_id]
            else:
                instance = StrategySchedule(trading_account_id=resolved_account_id)
                db.add(instance)
                db.flush()

            for field, value in data.items():
                setattr(instance, field, value)

            instance.trading_account_id = resolved_account_id
            instance.next_run_at = self._compute_next_run_at(instance.cron_expression)
            db.add(instance)
            db.flush()
            keep_ids.add(instance.id)

        for schedule_id, instance in existing.items():
            if schedule_id not in keep_ids:
                db.delete(instance)

        db.commit()
        logger.info(
            "schedules replaced: account_id=%s kept=%s, deleted=%s",
            resolved_account_id,
            keep_ids,
            set(existing.keys()) - keep_ids,
        )
        return self.list_schedules(db, account_id=resolved_account_id)

    def list_runs(
        self,
        db: Session,
        limit: int = 20,
        run_date: date | None = None,
        status: str | None = None,
        before_id: int | None = None,
        account_id: int | None = None,
    ) -> list[StrategyRun]:
        resolved_account_id = self._resolve_account_id(db, account_id)
        stmt = select(StrategyRun).where(
            StrategyRun.trading_account_id == resolved_account_id
        )

        if run_date is not None:
            start_of_day = datetime.combine(run_date, datetime.min.time())
            end_of_day = start_of_day + timedelta(days=1)
            stmt = stmt.where(
                StrategyRun.started_at >= start_of_day,
                StrategyRun.started_at < end_of_day,
            )

        normalized_status = str(status or "").strip().lower()
        if normalized_status:
            stmt = stmt.where(StrategyRun.status == normalized_status)

        if before_id is not None:
            stmt = stmt.where(StrategyRun.id < before_id)

        stmt = stmt.order_by(StrategyRun.started_at.desc(), StrategyRun.id.desc()).limit(
            limit
        )
        runs = list(db.scalars(stmt).all())
        for run in runs:
            self._hydrate_run_datetimes(run, include_display_fields=False)
        return runs

    def list_runs_page(
        self,
        db: Session,
        limit: int = 20,
        run_date: date | None = None,
        status: str | None = None,
        before_id: int | None = None,
        account_id: int | None = None,
    ) -> dict[str, Any]:
        page_size = max(1, limit)
        runs = self.list_runs(
            db,
            limit=page_size + 1,
            run_date=run_date,
            status=status,
            before_id=before_id,
            account_id=account_id,
        )
        has_more = len(runs) > page_size
        items = runs[:page_size]
        next_before_id = items[-1].id if has_more and items else None
        return {
            "items": items,
            "next_before_id": next_before_id,
            "has_more": has_more,
        }

    def get_runtime_overview(
        self, db: Session, account_id: int | None = None
    ) -> dict[str, Any]:
        runs = self.list_runs(db, limit=100, account_id=account_id)
        latest_run = runs[0] if runs else None
        return {
            "last_run": self._build_runtime_last_run(latest_run),
            "today": self._build_runtime_summary_section(
                [run for run in runs if self._is_within_days(run.started_at, 1, same_day_only=True)]
            ),
            "recent_3_days": self._build_runtime_summary_section(
                [run for run in runs if self._is_within_days(run.started_at, 3)]
            ),
            "recent_7_days": self._build_runtime_summary_section(
                [run for run in runs if self._is_within_days(run.started_at, 7)]
            ),
        }

    def get_run(
        self, db: Session, run_id: int, account_id: int | None = None
    ) -> StrategyRun | None:
        resolved_account_id = self._resolve_account_id(db, account_id)
        stmt = (
            select(StrategyRun)
            .where(
                StrategyRun.id == run_id,
                StrategyRun.trading_account_id == resolved_account_id,
            )
            .options(selectinload(StrategyRun.trade_orders))
        )
        run = db.scalar(stmt)
        if run is not None:
            self._hydrate_run_datetimes(run, include_display_fields=True)
        return run

    def get_run_raw_tool_preview(
        self,
        db: Session,
        run_id: int,
        preview_index: int,
        account_id: int | None = None,
    ) -> dict[str, Any]:
        run = self.get_run(db, run_id, account_id=account_id)
        if run is None:
            raise LookupError("运行记录不存在。")

        preview = self._build_raw_tool_preview_by_index(run, preview_index)
        if preview is None:
            raise LookupError("原始工具预览不存在。")
        return preview

    def get_persistent_session(
        self, db: Session, account_id: int | None = None
    ) -> PersistentSessionRead:
        resolved_account_id = self._resolve_account_id(db, account_id)
        session = self._get_or_create_persistent_session(
            db, trading_account_id=resolved_account_id
        )
        total_count = db.execute(
            select(func.count(ChatMessageRecord.id)).where(
                ChatMessageRecord.session_id == session.id
            )
        ).scalar_one()
        return PersistentSessionRead(
            id=session.id,
            title=session.title,
            kind=str(session.kind or "automation"),
            slug=session.slug,
            created_at=_assume_utc(session.created_at),
            updated_at=_assume_utc(session.updated_at),
            last_message_at=_assume_utc(session.last_message_at),
            message_count=int(total_count),
            archived_summary=session.archived_summary,
            summary_revision=int(session.summary_revision or 0),
            last_compacted_message_id=session.last_compacted_message_id,
            last_compacted_run_id=session.last_compacted_run_id,
        )

    def list_persistent_session_messages(
        self,
        db: Session,
        *,
        limit: int = 50,
        before_id: int | None = None,
        account_id: int | None = None,
    ) -> tuple[PersistentSessionRead, list[ChatMessageRead], int | None, bool]:
        resolved_account_id = self._resolve_account_id(db, account_id)
        session = self._get_or_create_persistent_session(
            db, trading_account_id=resolved_account_id
        )
        page_size = max(1, int(limit))
        total_count = db.execute(
            select(func.count(ChatMessageRecord.id)).where(
                ChatMessageRecord.session_id == session.id
            )
        ).scalar_one()

        stmt = (
            select(ChatMessageRecord)
            .where(ChatMessageRecord.session_id == session.id)
            .order_by(ChatMessageRecord.id.desc())
        )
        if before_id is not None:
            stmt = stmt.where(ChatMessageRecord.id < before_id)

        records = (
            db.execute(stmt.limit(page_size + 1)).scalars().all()
        )
        has_more = len(records) > page_size
        if has_more:
            records = records[:page_size]
        records.reverse()
        next_before_id = records[0].id if has_more and records else None

        session_read = PersistentSessionRead(
            id=session.id,
            title=session.title,
            kind=str(session.kind or "automation"),
            slug=session.slug,
            created_at=_assume_utc(session.created_at),
            updated_at=_assume_utc(session.updated_at),
            last_message_at=_assume_utc(session.last_message_at),
            message_count=int(total_count),
            archived_summary=session.archived_summary,
            summary_revision=int(session.summary_revision or 0),
            last_compacted_message_id=session.last_compacted_message_id,
            last_compacted_run_id=session.last_compacted_run_id,
        )
        return (
            session_read,
            [
                ChatMessageRead(
                    id=record.id,
                    role=record.role,
                    content=record.content,
                    tool_calls=record.tool_calls,
                    attachments=None,
                    created_at=_assume_utc(record.created_at),
                )
                for record in records
            ],
            next_before_id,
            has_more,
        )

    def delete_run(
        self,
        db: Session,
        run_id: int,
        *,
        force: bool = False,
        account_id: int | None = None,
    ) -> None:
        resolved_account_id = self._resolve_account_id(db, account_id)
        run = db.scalar(
            select(StrategyRun).where(
                StrategyRun.id == run_id,
                StrategyRun.trading_account_id == resolved_account_id,
            )
        )
        if run is None:
            raise LookupError("运行记录不存在。")
        if str(run.status or "").strip().lower() in {"running", "pending"}:
            if not force:
                raise RuntimeError("运行中的任务不可删除，请等待任务结束后重试。")
            if self._get_account_run_lock(resolved_account_id).locked():
                raise RuntimeError("当前账户仍有任务正在执行，暂不能强制删除，请稍后重试。")

        related_session_id = run.chat_session_id
        db.execute(delete(ChatMessageRecord).where(ChatMessageRecord.run_id == run_id))
        db.delete(run)

        if related_session_id is not None:
            session = db.get(ChatSession, related_session_id)
            if session is not None:
                last_message = db.scalar(
                    select(ChatMessageRecord)
                    .where(ChatMessageRecord.session_id == related_session_id)
                    .order_by(ChatMessageRecord.id.desc())
                    .limit(1)
                )
                session.last_message_at = (
                    _assume_utc(last_message.created_at) if last_message is not None else None
                )
                db.add(session)

        db.commit()

    def _hydrate_run_datetimes(
        self, run: StrategyRun, *, include_display_fields: bool
    ) -> None:
        run.started_at = _assume_utc(run.started_at)
        run.finished_at = _assume_utc(run.finished_at)
        run.run_type = self._infer_run_type(run)
        self._hydrate_run_summary_metrics(run)
        if include_display_fields:
            self._hydrate_run_display_fields(run)
            for order in run.trade_orders:
                order.created_at = _assume_utc(order.created_at)

    def _hydrate_run_summary_metrics(self, run: StrategyRun) -> None:
        token_usage = self._get_run_token_usage(run)
        run.api_call_count = self._count_run_api_calls(run)
        run.executed_trade_count = self._count_executed_actions(run)
        run.input_tokens = token_usage["input"]
        run.output_tokens = token_usage["output"]
        run.total_tokens = token_usage["total"]

    def _hydrate_run_display_fields(self, run: StrategyRun) -> None:
        run.output_markdown = (
            str(run.final_answer or run.analysis_summary or run.error_message or "").strip()
            or None
        )
        run.api_details = self._build_run_api_details(run)
        run.raw_tool_previews = self._build_raw_tool_previews(run)
        run.trade_details = self._build_run_trade_details(run)

    def _format_token_count(self, value: int) -> str:
        if not isinstance(value, int) or value <= 0:
            return "--"
        if value >= 1000:
            return f"{value / 1000:.1f}k"
        return str(value)

    def _get_duration_text(
        self, started_at: datetime | None, finished_at: datetime | None
    ) -> str:
        if started_at is None or finished_at is None:
            return "进行中" if started_at is not None and finished_at is None else "--"
        start = _assume_utc(started_at)
        end = _assume_utc(finished_at)
        if start is None or end is None or end <= start:
            return "--"
        total_seconds = int((end - start).total_seconds())
        minutes = total_seconds // 60
        seconds = total_seconds % 60
        return f"{minutes}分{seconds:02d}秒"

    def _get_runtime_status_text(self, status: str | None) -> str:
        if status == "completed":
            return "正常"
        if status == "failed":
            return "失败"
        if status == "running":
            return "进行中"
        return "暂无记录"

    def _is_within_days(
        self,
        started_at: datetime | None,
        days: int,
        *,
        same_day_only: bool = False,
    ) -> bool:
        timestamp = _assume_utc(started_at)
        if timestamp is None:
            return False
        now = now_utc()
        if same_day_only:
            local_started = timestamp.astimezone(SHANGHAI_TZ)
            local_now = now.astimezone(SHANGHAI_TZ)
            return local_started.date() == local_now.date()
        return now - timestamp <= timedelta(days=days)

    def _build_runtime_last_run(self, run: StrategyRun | None) -> dict[str, Any]:
        if run is None:
            return {
                "start_time": "--",
                "end_time": "--",
                "status": "idle",
                "status_text": "暂无记录",
                "duration": "--",
                "input_tokens": "--",
                "output_tokens": "--",
                "total_tokens": "--",
            }

        return {
            "start_time": run.started_at.isoformat() if run.started_at else "--",
            "end_time": run.finished_at.isoformat() if run.finished_at else "--",
            "status": run.status,
            "status_text": self._get_runtime_status_text(run.status),
            "duration": self._get_duration_text(run.started_at, run.finished_at),
            "input_tokens": self._format_token_count(int(run.input_tokens or 0)),
            "output_tokens": self._format_token_count(int(run.output_tokens or 0)),
            "total_tokens": self._format_token_count(int(run.total_tokens or 0)),
        }

    def _build_runtime_summary_section(
        self, runs: list[StrategyRun]
    ) -> dict[str, Any]:
        analysis_count = len(runs)
        success_count = sum(1 for run in runs if run.status == "completed")
        api_calls = sum(int(run.api_call_count or 0) for run in runs)
        trades = sum(int(run.executed_trade_count or 0) for run in runs)
        input_tokens = sum(int(run.input_tokens or 0) for run in runs)
        output_tokens = sum(int(run.output_tokens or 0) for run in runs)
        total_tokens = sum(int(run.total_tokens or 0) for run in runs)
        return {
            "analysis_count": analysis_count,
            "api_calls": api_calls,
            "trades": trades,
            "success_rate": round((success_count / analysis_count) * 100, 1)
            if analysis_count > 0
            else 0.0,
            "input_tokens": self._format_token_count(input_tokens),
            "output_tokens": self._format_token_count(output_tokens),
            "total_tokens": self._format_token_count(total_tokens),
        }

    def _get_api_tool_text(self, name: str) -> dict[str, str]:
        mapping = {
            "mx_get_positions": {"name": "获取持仓", "summary": "读取当前账户持仓与仓位分布。"},
            "mx_get_balance": {"name": "获取资产", "summary": "读取账户总资产、现金和收益情况。"},
            "mx_get_orders": {"name": "获取委托", "summary": "读取近期委托和成交记录，用于判断交易状态。"},
            "mx_get_self_selects": {"name": "获取自选", "summary": "读取当前自选股列表，辅助观察候选标的。"},
            "mx_query_market": {"name": "查询行情", "summary": "获取目标股票的实时行情和基础市场数据。"},
            "mx_search_news": {"name": "搜索资讯", "summary": "查询相关新闻或公告，辅助判断市场事件影响。"},
            "mx_screen_stocks": {"name": "筛选股票", "summary": "按条件筛选候选标的，缩小分析范围。"},
            "mx_manage_self_select": {"name": "管理自选", "summary": "增删自选股，维护后续关注列表。"},
            "mx_moni_trade": {"name": "提交模拟交易", "summary": "向模拟交易系统提交买入或卖出指令。"},
            "mx_moni_cancel": {"name": "撤销委托", "summary": "撤销尚未完成的模拟委托单。"},
        }
        return mapping.get(name, {"name": name or "未命名调用", "summary": "执行一次系统或妙想工具调用。"})

    def _build_run_api_details(self, run: StrategyRun) -> list[dict[str, Any]]:
        trade_tool_names = {"mx_moni_trade", "mx_moni_cancel"}
        results: list[dict[str, Any]] = []
        for idx, item in enumerate(self._get_detail_tool_calls(run)):
            tool_name = str(item.get("name") or "")
            if tool_name in trade_tool_names:
                continue
            tool_text = self._get_api_tool_text(tool_name)
            result = item.get("result")
            ok: bool | None = None
            status = "done"
            if isinstance(result, dict) and "ok" in result:
                ok = bool(result.get("ok"))
                status = "done" if ok else "failed"
            results.append(
                {
                    "tool_name": tool_name,
                    "name": tool_text["name"],
                    "summary": tool_text["summary"],
                    "preview_index": idx,
                    "tool_call_id": str(
                        item.get("id") or item.get("tool_call_id") or ""
                    )
                    or None,
                    "status": status,
                    "ok": ok,
                }
            )
        return results

    def _build_raw_tool_previews(self, run: StrategyRun) -> list[dict[str, Any]]:
        previews: list[dict[str, Any]] = []
        for idx, item in enumerate(self._get_detail_tool_calls(run)):
            preview = self._build_raw_tool_preview_item(item, idx)
            if preview is not None:
                previews.append(preview)
        return previews

    def _build_raw_tool_preview_by_index(
        self, run: StrategyRun, preview_index: int
    ) -> dict[str, Any] | None:
        for idx, item in enumerate(self._get_detail_tool_calls(run)):
            if idx != preview_index:
                continue
            return self._build_raw_tool_preview_item(item, idx, truncate=False)
        return None

    def _build_raw_tool_preview_item(
        self,
        item: dict[str, Any],
        preview_index: int,
        *,
        truncate: bool = True,
    ) -> dict[str, Any] | None:
        tool_name = str(item.get("name") or "")
        tool_text = self._get_api_tool_text(tool_name)
        result = item.get("result")
        if not isinstance(result, dict):
            return None
        raw_payload = result.get("result")
        preview_source = raw_payload if raw_payload is not None else result
        full_preview = self._format_tool_preview(preview_source, truncate=False)
        truncated = len(full_preview) > RAW_TOOL_PREVIEW_MAX_CHARS
        preview = self._format_tool_preview(preview_source) if truncate else full_preview
        return {
            "preview_index": preview_index,
            "tool_name": tool_name,
            "display_name": tool_text["name"],
            "summary": str(result.get("summary") or tool_text["summary"]),
            "preview": preview,
            "truncated": truncated if truncate else False,
            "full_preview": full_preview,
        }

    def _format_tool_preview(
        self,
        payload: Any,
        max_chars: int = RAW_TOOL_PREVIEW_MAX_CHARS,
        *,
        truncate: bool = True,
    ) -> str:
        try:
            text = json.dumps(payload, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            text = str(payload)
        text = text.strip()
        if not truncate or len(text) <= max_chars:
            return text
        return text[: max_chars - 16].rstrip() + "\n...\n<已截断>"

    def _extract_trade_name(self, payload: Any) -> str:
        if not isinstance(payload, dict):
            return ""

        candidates = [
            payload.get("name"),
            payload.get("stock_name"),
            payload.get("stockName"),
            payload.get("security_name"),
            payload.get("securityName"),
        ]
        for candidate in candidates:
            value = str(candidate or "").strip()
            if value:
                return value

        result = payload.get("result")
        if result is not payload:
            return self._extract_trade_name(result)
        return ""

    def _get_trade_summary(
        self,
        action: str,
        symbol: str,
        volume: int,
    ) -> str:
        action_text = "卖出" if action == "sell" else "买入"
        display_symbol = symbol or "--"
        return f"挂单{action_text}{display_symbol}共计{volume}股。"

    def _resolve_trade_detail_status(self, raw_status: Any) -> tuple[str, bool | None]:
        text = str(raw_status or "").strip().lower()
        if text and any(flag in text for flag in ("fail", "error", "reject")):
            return "failed", False
        return "done", True

    def _build_run_trade_details(self, run: StrategyRun) -> list[dict[str, Any]]:
        tool_calls = self._get_detail_tool_calls(run)
        if run.trade_orders:
            details: list[dict[str, Any]] = []
            for order in run.trade_orders:
                action_name = str(order.action).upper()
                trade_action = "sell" if action_name == "SELL" else "buy"
                tool_name = self._match_trade_tool_name(tool_calls, order.symbol, action_name)
                detail_status, detail_ok = self._resolve_trade_detail_status(order.status)
                details.append(
                    {
                        "action": trade_action,
                        "action_text": "模拟卖出" if action_name == "SELL" else "模拟买入",
                        "symbol": order.symbol,
                        "name": self._extract_trade_name(order.response_payload) or order.symbol,
                        "volume": int(order.quantity),
                        "price": order.price,
                        "amount": round(float(order.price or 0) * int(order.quantity), 2)
                        if order.price is not None
                        else None,
                        "summary": self._get_trade_summary(trade_action, order.symbol, int(order.quantity)),
                        "tool_name": tool_name,
                        "preview_index": self._find_tool_call_index(tool_calls, tool_name, order.symbol),
                        "status": detail_status,
                        "ok": detail_ok,
                    }
                )
            return details

        executed_actions = run.executed_actions if isinstance(run.executed_actions, list) else []
        details: list[dict[str, Any]] = []
        for action in executed_actions:
            if not isinstance(action, dict):
                continue
            action_name = str(action.get("action") or "").upper()
            if action_name not in {"BUY", "SELL"}:
                continue
            trade_action = "sell" if action_name == "SELL" else "buy"
            price = _parse_float(action.get("price"))
            volume = int(action.get("quantity") or 0)
            symbol = str(action.get("symbol") or "--")
            tool_name = self._match_trade_tool_name(tool_calls, symbol, action_name)
            detail_status, detail_ok = self._resolve_trade_detail_status(
                action.get("status")
            )
            details.append(
                {
                    "action": trade_action,
                    "action_text": "模拟卖出" if action_name == "SELL" else "模拟买入",
                    "symbol": symbol,
                    "name": str(action.get("name") or "").strip() or symbol,
                    "volume": volume,
                    "price": price,
                    "amount": round((price or 0) * volume, 2) if price is not None else None,
                    "summary": self._get_trade_summary(trade_action, symbol, volume),
                    "tool_name": tool_name,
                    "preview_index": self._find_tool_call_index(tool_calls, tool_name, symbol),
                    "status": detail_status,
                    "ok": detail_ok,
                }
            )
        return details

    def _match_trade_tool_name(
        self, tool_calls: list[dict[str, Any]], symbol: str, action_name: str
    ) -> str | None:
        desired_name = "mx_moni_trade"
        target_symbol = str(symbol or "").strip()
        target_action = str(action_name or "").upper()
        for item in reversed(tool_calls):
            tool_name = str(item.get("name") or "")
            if tool_name != desired_name:
                continue
            result = item.get("result")
            if not isinstance(result, dict):
                continue
            executed_action = result.get("executed_action")
            if not isinstance(executed_action, dict):
                continue
            if str(executed_action.get("symbol") or "").strip() != target_symbol:
                continue
            if str(executed_action.get("action") or "").upper() != target_action:
                continue
            return tool_name
        return None

    def _find_tool_call_index(
        self,
        tool_calls: list[dict[str, Any]],
        tool_name: str | None,
        symbol: str | None = None,
    ) -> int | None:
        if not tool_name:
            return None
        target_symbol = str(symbol or "").strip()
        for idx in range(len(tool_calls) - 1, -1, -1):
            item = tool_calls[idx]
            if str(item.get("name") or "") != tool_name:
                continue
            if not target_symbol:
                return idx
            result = item.get("result")
            if not isinstance(result, dict):
                continue
            executed_action = result.get("executed_action")
            if isinstance(executed_action, dict) and str(executed_action.get("symbol") or "").strip() == target_symbol:
                return idx
        return None

    def _get_run_token_usage(self, run: StrategyRun) -> dict[str, int | None]:
        response_usage = self._extract_usage(run.llm_response_payload)
        request_usage = self._extract_usage(run.llm_request_payload)

        prompt_tokens = self._coerce_token_value(
            _coalesce(
                response_usage.get("prompt_tokens") if response_usage is not None else None,
                request_usage.get("prompt_tokens") if request_usage is not None else None,
            )
        )
        completion_tokens = self._coerce_token_value(
            _coalesce(
                response_usage.get("completion_tokens")
                if response_usage is not None
                else None,
                request_usage.get("completion_tokens")
                if request_usage is not None
                else None,
            )
        )
        total_tokens = self._coerce_token_value(
            _coalesce(
                response_usage.get("total_tokens") if response_usage is not None else None,
                request_usage.get("total_tokens") if request_usage is not None else None,
            )
        )

        if total_tokens is None and (
            prompt_tokens is not None or completion_tokens is not None
        ):
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

        return {
            "input": prompt_tokens,
            "output": completion_tokens,
            "total": total_tokens,
        }

    def _extract_usage(self, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None

        direct_usage = payload.get("usage")
        if isinstance(direct_usage, dict):
            return direct_usage

        responses = payload.get("responses")
        if not isinstance(responses, list):
            return None

        for item in reversed(responses):
            if not isinstance(item, dict):
                continue
            usage = item.get("usage")
            if isinstance(usage, dict):
                return usage
        return None

    def _coerce_token_value(self, value: Any) -> int | None:
        numeric = _parse_float(value)
        if numeric is None or numeric <= 0:
            return None
        return int(numeric)

    def _count_run_api_calls(self, run: StrategyRun) -> int:
        trade_tool_names = {"mx_moni_trade", "mx_moni_cancel"}
        return sum(
            1
            for item in self._get_detail_tool_calls(run)
            if str(item.get("name") or "") not in trade_tool_names
        )

    def _count_executed_actions(self, run: StrategyRun) -> int:
        executed_actions = (
            run.executed_actions if isinstance(run.executed_actions, list) else []
        )
        trade_actions = {"BUY", "SELL"}
        return sum(
            1
            for item in executed_actions
            if isinstance(item, dict)
            and str(item.get("action") or "").upper() in trade_actions
        )

    def _get_detail_tool_calls(self, run: StrategyRun) -> list[dict[str, Any]]:
        skill_payloads = (
            run.skill_payloads if isinstance(run.skill_payloads, dict) else {}
        )
        decision_payload = (
            run.decision_payload if isinstance(run.decision_payload, dict) else {}
        )

        tool_calls = skill_payloads.get("tool_calls")
        if not isinstance(tool_calls, list):
            tool_calls = decision_payload.get("tool_calls")
        if not isinstance(tool_calls, list):
            return []
        return [item for item in tool_calls if isinstance(item, dict)]

    def _empty_account_overview(self, errors: list[str] | None = None) -> dict[str, Any]:
        return {
            "open_date": None,
            "operating_days": None,
            "initial_capital": None,
            "total_assets": None,
            "total_market_value": None,
            "cash_balance": None,
            "total_position_ratio": None,
            "holding_profit": None,
            "total_return_ratio": None,
            "nav": None,
            "daily_profit": None,
            "daily_return_ratio": None,
            "positions": [],
            "orders": [],
            "trade_summaries": [],
            "errors": errors or [],
        }

    def _with_account_raw(
        self,
        overview: dict[str, Any],
        *,
        include_raw: bool,
        balance_result: dict[str, Any] | None,
        positions_result: dict[str, Any] | None,
        orders_result: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if include_raw:
            overview["raw_balance"] = balance_result
            overview["raw_positions"] = positions_result
            overview["raw_orders"] = orders_result
        return overview

    def _build_account_response(
        self,
        *,
        balance_result: dict[str, Any] | None,
        positions_result: dict[str, Any] | None,
        orders_result: dict[str, Any] | None,
        errors: list[str],
        include_raw: bool,
    ) -> dict[str, Any]:
        if (
            balance_result is None
            and positions_result is None
            and orders_result is None
        ):
            return self._with_account_raw(
                self._empty_account_overview(errors),
                include_raw=include_raw,
                balance_result=balance_result,
                positions_result=positions_result,
                orders_result=orders_result,
            )

        overview = self._build_account_overview(balance_result, positions_result)
        normalized_orders = self._build_orders_overview(orders_result)
        overview["orders"] = normalized_orders
        overview["trade_summaries"] = self._build_trade_summaries(
            normalized_orders,
            overview.get("positions") or [],
        )
        overview["errors"] = errors
        return self._with_account_raw(
            overview,
            include_raw=include_raw,
            balance_result=balance_result,
            positions_result=positions_result,
            orders_result=orders_result,
        )

    def _get_cached_account_overview(self, account_id: int) -> dict[str, Any] | None:
        with self._account_cache_lock:
            cached = self._account_overview_cache.get(account_id)
            expires_at = self._account_overview_cache_expires_at.get(account_id)
            if cached is None or expires_at is None or expires_at <= now_utc():
                self._account_overview_cache.pop(account_id, None)
                self._account_overview_cache_expires_at.pop(account_id, None)
                return None

            return dict(cached)

    def _set_cached_account_overview(
        self, account_id: int, overview: dict[str, Any]
    ) -> None:
        ttl_seconds = max(0, int(get_settings().account_overview_cache_ttl_seconds))
        if ttl_seconds <= 0:
            with self._account_cache_lock:
                self._account_overview_cache.pop(account_id, None)
                self._account_overview_cache_expires_at.pop(account_id, None)
            return

        # Keep debug-only upstream payloads out of the process cache to reduce
        # steady-state RSS while preserving the normalized account summary.
        cached_overview = dict(overview)
        cached_overview.pop("raw_balance", None)
        cached_overview.pop("raw_positions", None)
        cached_overview.pop("raw_orders", None)

        with self._account_cache_lock:
            self._account_overview_cache[account_id] = cached_overview
            self._account_overview_cache_expires_at[account_id] = now_utc() + timedelta(
                seconds=ttl_seconds
            )

    def _fetch_live_account_payloads(
        self, client: MXClient
    ) -> dict[str, dict[str, Any]]:
        return {
            "balance": self._safe_call(client.get_balance),
            "positions": self._safe_call(client.get_positions),
            "orders": self._safe_call(client.get_orders),
        }

    def _extract_tool_result(
        self, tool_calls: list[dict[str, Any]], tool_name: str
    ) -> dict[str, Any] | None:
        for item in reversed(tool_calls):
            if item.get("name") != tool_name:
                continue
            result = item.get("result")
            if not isinstance(result, dict) or not result.get("ok"):
                continue
            payload = result.get("result")
            if isinstance(payload, dict):
                return payload
        return None

    def _project_balance_result(
        self,
        balance_result: dict[str, Any] | None,
        *,
        app_settings: Any,
    ) -> dict[str, Any] | None:
        """Seal balance payload once so overview/chat share agent operable funds."""
        if not isinstance(balance_result, dict):
            return balance_result
        from skills.mx_core.capital_seal import apply_seal_to_balance_payload, get_seal_config

        enabled, seal = get_seal_config(app_settings)
        projected = apply_seal_to_balance_payload(
            balance_result,
            seal_amount=seal,
            enabled=enabled,
        )
        return projected if isinstance(projected, dict) else balance_result

    def _stamp_overview_capital_seal(
        self,
        overview: dict[str, Any],
        balance_result: dict[str, Any] | None,
        *,
        app_settings: Any,
    ) -> dict[str, Any]:
        from skills.mx_core.capital_seal import get_seal_config

        enabled, seal = get_seal_config(app_settings)
        if not enabled or not isinstance(overview, dict):
            return overview
        meta = None
        if isinstance(balance_result, dict):
            raw_meta = balance_result.get("_aniu_capital_seal")
            if isinstance(raw_meta, dict):
                meta = dict(raw_meta)
        if meta is None:
            meta = {
                "applied": True,
                "seal_amount": seal,
                "real_total_assets": None,
                "real_cash_balance": None,
                "virtual_total_assets": overview.get("total_assets"),
                "virtual_cash_balance": overview.get("cash_balance"),
                "seal_breached": False,
            }
        overview = dict(overview)
        overview["capital_seal"] = {
            **meta,
            "enabled": True,
            "seal_amount": seal,
        }
        return overview

    def _finalize_account_overview(
        self,
        *,
        balance_result: dict[str, Any] | None,
        positions_result: dict[str, Any] | None,
        orders_result: dict[str, Any] | None,
        errors: list[str],
        include_raw: bool,
        app_settings: Any,
    ) -> dict[str, Any]:
        projected_balance = self._project_balance_result(
            balance_result, app_settings=app_settings
        )
        overview = self._build_account_response(
            balance_result=projected_balance,
            positions_result=positions_result,
            orders_result=orders_result,
            errors=errors,
            include_raw=include_raw,
        )
        return self._stamp_overview_capital_seal(
            overview,
            projected_balance if isinstance(projected_balance, dict) else None,
            app_settings=app_settings,
        )

    def get_account_overview(
        self,
        account_id: int | None = None,
        *,
        include_raw: bool = False,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """读取单个账户的总览（账户级缓存，§10.2）。

        妙想 Client 使用该账户 Key；接口失败时只回退当前账户快照；
        资金封印使用当前账户配置。
        """
        with session_scope() as db:
            resolved_account_id = self._resolve_account_id(db, account_id)
            account = account_service.require_account(db, resolved_account_id)
            settings = self.get_or_create_settings(db)

            account_settings = SimpleNamespace(
                mx_api_key=str(account.mx_api_key or "").strip() or None,
                mx_api_url=str(get_settings().mx_api_url or "").strip() or None,
                allowed_markets_json=account.allowed_markets_json,
                capital_seal_enabled=bool(account.capital_seal_enabled),
                capital_seal_amount=float(account.capital_seal_amount or 0),
            )
            cached_balance_result, cached_positions_result, cached_orders_result = (
                self._get_recent_account_snapshot(
                    db, account_id=resolved_account_id
                )
            )

        if not force_refresh and not include_raw:
            cached_overview = self._get_cached_account_overview(resolved_account_id)
            if cached_overview is not None:
                return cached_overview

        errors: list[str] = []
        balance_result = cached_balance_result
        positions_result = cached_positions_result
        orders_result = cached_orders_result
        client: MXClient | None = None
        mx_client_config = build_skill_context(
            run_type="chat",
            app_settings=account_settings,
        )["mx_client_config"]

        if mx_client_config.get("api_key"):
            try:
                client = MXClient(
                    api_key=mx_client_config.get("api_key"),
                    base_url=mx_client_config.get("base_url"),
                )
            except Exception as exc:
                if (
                    balance_result is None
                    and positions_result is None
                    and orders_result is None
                ):
                    return self._finalize_account_overview(
                        balance_result=None,
                        positions_result=None,
                        orders_result=None,
                        errors=[str(exc)],
                        include_raw=include_raw,
                        app_settings=account_settings,
                    )

                errors.append(f"{str(exc)}，当前展示最近一次任务缓存的账户数据。")
                return self._finalize_account_overview(
                    balance_result=balance_result,
                    positions_result=positions_result,
                    orders_result=orders_result,
                    errors=errors,
                    include_raw=include_raw,
                    app_settings=account_settings,
                )

        try:
            if client is not None:
                live_payloads = self._fetch_live_account_payloads(client)

                balance_payload = live_payloads["balance"]
                if not balance_payload.get("ok"):
                    if cached_balance_result is not None:
                        balance_result = cached_balance_result
                        errors.append(
                            f"{str(balance_payload.get('error') or '资金接口失败')}，当前展示最近一次任务缓存的账户资金。"
                        )
                    else:
                        balance_result = None
                        errors.append(
                            str(balance_payload.get("error") or "资金接口失败")
                        )
                else:
                    balance_result = balance_payload.get("result")

                positions_payload = live_payloads["positions"]
                if not positions_payload.get("ok"):
                    if cached_positions_result is not None:
                        positions_result = cached_positions_result
                        errors.append(
                            f"{str(positions_payload.get('error') or '持仓接口失败')}，当前展示最近一次任务缓存的持仓数据。"
                        )
                    else:
                        positions_result = None
                        errors.append(
                            str(positions_payload.get("error") or "持仓接口失败")
                        )
                else:
                    positions_result = positions_payload.get("result")

                orders_payload = live_payloads["orders"]
                if not orders_payload.get("ok"):
                    if cached_orders_result is not None:
                        orders_result = cached_orders_result
                        errors.append(
                            f"{str(orders_payload.get('error') or '委托接口失败')}，当前展示最近一次任务缓存的委托数据。"
                        )
                    else:
                        orders_result = None
                        errors.append(
                            str(orders_payload.get("error") or "委托接口失败")
                        )
                else:
                    orders_result = orders_payload.get("result")
            elif (
                balance_result is None
                and positions_result is None
                and orders_result is None
            ):
                return self._finalize_account_overview(
                    balance_result=None,
                    positions_result=None,
                    orders_result=None,
                    errors=errors
                    or ["未配置 MX API Key，且没有可用缓存账户数据。"],
                    include_raw=include_raw,
                    app_settings=account_settings,
                )
        finally:
            if client is not None:
                client.close()

        overview = self._finalize_account_overview(
            balance_result=balance_result,
            positions_result=positions_result,
            orders_result=orders_result,
            errors=errors,
            include_raw=include_raw,
            app_settings=account_settings,
        )
        # Cache sealed overview so chat tools and UI share the same operable funds.
        self._set_cached_account_overview(
            resolved_account_id,
            self._finalize_account_overview(
                balance_result=balance_result,
                positions_result=positions_result,
                orders_result=orders_result,
                errors=errors,
                include_raw=False,
                app_settings=account_settings,
            ),
        )
        return overview

    def get_global_overview(
        self,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        """全局聚合总览（§10.3）：各账户独立刷新，金额求和，收益率加权。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with session_scope() as db:
            accounts = account_service.list_accounts(db, include_archived=False)

        accounts_payload: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        def _fetch(account: TradingAccount) -> dict[str, Any]:
            try:
                overview = self.get_account_overview(
                    account.id, force_refresh=force_refresh
                )
                return {
                    "account_id": account.id,
                    "account_name": account.name,
                    "status": "ok",
                    "overview": overview,
                }
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "global overview account failed: account_id=%s error=%s",
                    account.id,
                    exc,
                )
                return {
                    "account_id": account.id,
                    "account_name": account.name,
                    "status": "error",
                    "error": str(exc),
                    "overview": self._empty_account_overview([str(exc)]),
                }

        if len(accounts) <= 1:
            for account in accounts:
                payload = _fetch(account)
                accounts_payload.append(payload)
                if payload["status"] != "ok":
                    errors.append(
                        {
                            "account_id": account.id,
                            "account_name": account.name,
                            "error": payload.get("error"),
                        }
                    )
        else:
            with ThreadPoolExecutor(
                max_workers=min(len(accounts), ACCOUNT_OVERVIEW_CACHE_MAX_WORKERS)
            ) as executor:
                futures = {
                    executor.submit(_fetch, account): account
                    for account in accounts
                }
                for future in as_completed(futures):
                    payload = future.result()
                    accounts_payload.append(payload)
                    if payload["status"] != "ok":
                        errors.append(
                            {
                                "account_id": payload["account_id"],
                                "account_name": payload["account_name"],
                                "error": payload.get("error"),
                            }
                        )

        accounts_payload.sort(key=lambda item: item["account_id"])

        def _numeric(
            item: dict[str, Any], key: str
        ) -> tuple[float, bool]:
            value = (item.get("overview") or {}).get(key)
            try:
                return float(value), True
            except (TypeError, ValueError):
                return 0.0, False

        initial_capital = 0.0
        total_assets = 0.0
        cash_balance = 0.0
        total_market_value = 0.0
        holding_profit = 0.0
        daily_profit = 0.0
        initial_capital_known = False
        total_assets_known = False
        for item in accounts_payload:
            initial_value, initial_ok = _numeric(item, "initial_capital")
            assets_value, assets_ok = _numeric(item, "total_assets")
            if initial_ok:
                initial_capital += initial_value
                initial_capital_known = True
            if assets_ok:
                total_assets += assets_value
                total_assets_known = True
            cash_balance += _numeric(item, "cash_balance")[0]
            total_market_value += _numeric(item, "total_market_value")[0]
            holding_profit += _numeric(item, "holding_profit")[0]
            daily_profit += _numeric(item, "daily_profit")[0]

        total_return_ratio = None
        if total_assets_known and initial_capital_known and initial_capital > 0:
            total_return_ratio = total_assets / initial_capital - 1
        daily_return_ratio = None
        if total_assets_known and total_assets - daily_profit > 0:
            daily_return_ratio = daily_profit / (total_assets - daily_profit)

        return {
            "accounts": accounts_payload,
            "aggregate": {
                "initial_capital": round(initial_capital, 2),
                "total_assets": round(total_assets, 2),
                "cash_balance": round(cash_balance, 2),
                "total_market_value": round(total_market_value, 2),
                "holding_profit": round(holding_profit, 2),
                "daily_profit": round(daily_profit, 2),
                "total_return_ratio": total_return_ratio,
                "daily_return_ratio": daily_return_ratio,
            },
            "errors": errors,
        }

    def _build_account_settings_snapshot(
        self,
        db: Session,
        account: TradingAccount,
        *,
        run_type: str,
        task_prompt: str | None = None,
    ) -> tuple[SimpleNamespace, dict[str, Any]]:
        """构造账户级 settings 快照 + skill context（运行链/聊天共用）。"""
        settings = self.get_or_create_settings(db)
        from app.services.account_context import resolve_llm_config

        llm_config = resolve_llm_config(account, settings)
        mx_api_key = str(account.mx_api_key or "").strip() or None
        snapshot = SimpleNamespace(
            account_id=account.id,
            account_name=str(account.name or "").strip() or f"账户{account.id}",
            run_type=run_type,
            task_prompt=task_prompt or "",
            mx_api_key=mx_api_key,
            mx_api_url=str(get_settings().mx_api_url or "").strip() or None,
            llm_base_url=llm_config.base_url,
            llm_api_key=llm_config.api_key,
            llm_model=llm_config.model,
            llm_reasoning_effort=llm_config.reasoning_effort,
            llm_max_retries=llm_config.max_retries,
            llm_enable_reasoning_content_echo=llm_config.enable_reasoning_content_echo,
            provider_name=llm_config.provider_name,
            system_prompt=str(account.system_prompt or ""),
            analyst_prompt=str(account.analyst_prompt or ""),
            market_query=str(account.market_query or ""),
            news_query=str(account.news_query or ""),
            screener_query=str(account.screener_query or ""),
            allowed_markets_json=account.allowed_markets_json,
            allowed_markets=normalize_allowed_markets(account.allowed_markets_json),
            max_actions=int(account.max_actions or 2),
            trade_enabled=bool(account.trade_enabled),
            disabled_skill_ids=parse_disabled_skill_ids(
                account.disabled_skill_ids_json
            ),
            automation_context_window_tokens=int(
                account.automation_context_window_tokens
                or AUTOMATION_DEFAULT_CONTEXT_WINDOW_TOKENS
            ),
            automation_recent_message_limit=int(
                account.automation_recent_message_limit
                or AUTOMATION_DEFAULT_RECENT_MESSAGE_LIMIT
            ),
            automation_enable_auto_compaction=bool(
                account.automation_enable_auto_compaction
            ),
            automation_idle_summary_hours=int(
                account.automation_idle_summary_hours
                or AUTOMATION_DEFAULT_IDLE_SUMMARY_HOURS
            ),
            capital_seal_enabled=bool(account.capital_seal_enabled),
            capital_seal_amount=float(account.capital_seal_amount or 0),
            app_display_name=getattr(settings, "app_display_name", "Aniu"),
            timeout_seconds=1800,
        )
        context = build_skill_context(
            run_type=run_type,
            app_settings=snapshot,
            trading_account_id=account.id,
            trading_account_name=snapshot.account_name,
        )
        return snapshot, context

    def chat(self, payload: ChatRequest) -> dict[str, Any]:
        with session_scope() as db:
            account = self._resolve_account(db, None)
            settings, tool_context = self._build_account_settings_snapshot(
                db, account, run_type="chat"
            )

        if not settings.llm_base_url or not settings.llm_api_key:
            raise RuntimeError("未配置大模型接口，无法执行 AI 聊天。")

        messages = [
            {"role": item.role, "content": item.content} for item in payload.messages
        ]

        content = llm_service.chat(
            model=settings.llm_model,
            base_url=str(settings.llm_base_url),
            api_key=str(settings.llm_api_key),
            system_prompt=settings.system_prompt,
            messages=messages,
            timeout_seconds=1800,
            tool_context=tool_context,
            enable_reasoning_echo=getattr(
                settings, "llm_enable_reasoning_content_echo", False
            ),
            reasoning_effort=getattr(settings, "llm_reasoning_effort", None),
        )

        return {
            "message": {
                "role": "assistant",
                "content": content,
            },
            "context": {
                "system_prompt_included": True,
                "tool_access_account_summary": True,
                "tool_access_positions": True,
                "tool_access_orders": True,
                "tool_access_runs": True,
            },
        }

    def chat_stream(self, payload: ChatRequest) -> Iterator[dict[str, Any]]:
        """Yield SSE-style events from the chat agent loop in real time.

        Runs the LLM agent loop on a worker thread and forwards emitted events
        to subscribers via an in-process queue. No StrategyRun is created."""
        with session_scope() as db:
            account = self._resolve_account(db, None)
            settings_snapshot, tool_context = self._build_account_settings_snapshot(
                db, account, run_type="chat"
            )

        if not settings_snapshot.llm_base_url or not settings_snapshot.llm_api_key:
            raise RuntimeError("未配置大模型接口，无法执行 AI 聊天。")

        messages = [
            {"role": item.role, "content": item.content} for item in payload.messages
        ]

        event_queue: queue.Queue[dict[str, Any] | None] = queue.Queue()
        cancel_event = Event()

        def _emit(event_type: str, **data: Any) -> None:
            event_queue.put(
                {"type": event_type, "ts": time.time(), **data}
            )

        def _worker() -> None:
            try:
                content = llm_service.chat(
                    model=settings_snapshot.llm_model,
                    base_url=settings_snapshot.llm_base_url,
                    api_key=settings_snapshot.llm_api_key,
                    system_prompt=settings_snapshot.system_prompt,
                    messages=messages,
                    timeout_seconds=180,
                    tool_context=tool_context,
                    emit=_emit,
                    cancel_event=cancel_event,
                    enable_reasoning_echo=getattr(
                        settings_snapshot, "llm_enable_reasoning_content_echo", False
                    ),
                    reasoning_effort=getattr(
                        settings_snapshot, "llm_reasoning_effort", None
                    ),
                )
                _emit("completed", message=content)
            except LLMStreamCancelled:
                logger.info("chat_stream worker cancelled")
            except Exception as exc:  # noqa: BLE001
                logger.exception("chat_stream worker failed")
                _emit(
                    "failed",
                    message=str(exc),
                    traceback=traceback.format_exc(limit=4),
                )
            finally:
                event_queue.put(None)

        worker = Thread(target=_worker, daemon=True, name="aniu-chat-stream")
        worker.start()

        terminal_event_seen = False
        try:
            while True:
                try:
                    event = event_queue.get(timeout=15.0)
                except queue.Empty:
                    yield {"type": "heartbeat", "ts": time.time()}
                    continue
                if event is None:
                    return
                if event.get("type") in {"completed", "failed"}:
                    terminal_event_seen = True
                yield event
                if terminal_event_seen:
                    # Drain any trailing events until sentinel so the thread exits cleanly.
                    while True:
                        trailing = event_queue.get()
                        if trailing is None:
                            return
        finally:
            cancel_event.set()
            worker.join(timeout=1.0)

    def _build_orders_overview(
        self, orders_payload: dict[str, Any] | None
    ) -> list[dict[str, Any]]:
        orders_source = (
            orders_payload.get("data") if isinstance(orders_payload, dict) else {}
        )
        if isinstance(orders_source, dict):
            rows = (
                orders_source.get("rows")
                or orders_source.get("list")
                or orders_source.get("orderList")
                or orders_source.get("orders")
                or []
            )
        else:
            rows = orders_source or []

        normalized_orders: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue

            side_value = str(
                row.get("orderDrt")
                or row.get("drt")
                or row.get("bsFlag")
                or row.get("side")
                or row.get("tradeType")
                or ""
            ).strip()
            side = "sell" if side_value in {"2", "SELL", "sell"} else "buy"

            status_raw = str(
                row.get("orderStatus")
                or row.get("status")
                or row.get("dbStatus")
                or "unknown"
            ).strip()

            raw_symbol = str(
                row.get("stockCode") or row.get("secCode") or row.get("code") or ""
            ).strip()
            market_code = row.get("secMkt")
            if market_code is None:
                market_code = row.get("market")
            suffix = _market_suffix(market_code)
            symbol = f"{raw_symbol}.{suffix}" if raw_symbol and suffix else raw_symbol

            order_quantity = int(
                _parse_float(
                    row.get("orderCount")
                    or row.get("count")
                    or row.get("quantity")
                    or row.get("orderQty")
                )
                or 0
            )
            filled_quantity = int(
                _parse_float(
                    row.get("dealCount")
                    or row.get("tradeCount")
                    or row.get("filledQuantity")
                    or row.get("filledQty")
                )
                or 0
            )

            normalized_orders.append(
                {
                    "order_id": str(
                        row.get("orderId")
                        or row.get("entrustNo")
                        or row.get("id")
                        or "--"
                    ),
                    "order_time": _format_timestamp(
                        row.get("orderTime")
                        or row.get("entrustTime")
                        or row.get("time")
                    ),
                    "name": str(
                        row.get("stockName")
                        or row.get("secName")
                        or row.get("name")
                        or "--"
                    ).strip(),
                    "symbol": symbol,
                    "side": side,
                    "side_text": "卖出" if side == "sell" else "买入",
                    "status": status_raw.lower(),
                    "status_text": _order_status_text(
                        status_raw,
                        filled_quantity=filled_quantity,
                        order_quantity=order_quantity,
                        db_status=row.get("dbStatus"),
                    ),
                    "order_price": _scaled_decimal(
                        row.get("orderPrice") or row.get("price"),
                        row.get("priceDec") or row.get("orderPriceDec"),
                    ),
                    "order_quantity": order_quantity,
                    "filled_price": _scaled_decimal(
                        row.get("dealPrice")
                        or row.get("tradePrice")
                        or row.get("filledPrice"),
                        row.get("priceDec") or row.get("dealPriceDec"),
                    ),
                    "filled_quantity": filled_quantity,
                }
            )

        return normalized_orders

    def _build_trade_summaries(
        self,
        orders: list[dict[str, Any]],
        positions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        active_symbols = {
            str(position.get("symbol") or "").strip()
            for position in positions
            if isinstance(position, dict)
            and str(position.get("symbol") or "").strip()
            and int(_parse_float(position.get("volume")) or 0) > 0
        }

        grouped_orders: dict[str, list[dict[str, Any]]] = {}
        for order in orders:
            if not isinstance(order, dict):
                continue
            symbol = str(order.get("symbol") or "").strip()
            if not symbol:
                continue
            grouped_orders.setdefault(symbol, []).append(order)

        summaries: list[dict[str, Any]] = []
        for symbol, symbol_orders in grouped_orders.items():
            buy_lots: list[dict[str, Any]] = []
            matched_quantity = 0
            matched_buy_amount = 0.0
            matched_sell_amount = 0.0
            first_buy_time: str | None = None
            last_exit_time: str | None = None
            name = "--"

            sorted_orders = sorted(
                symbol_orders,
                key=lambda item: (
                    str(item.get("order_time") or ""),
                    str(item.get("order_id") or ""),
                ),
            )

            for order in sorted_orders:
                filled_quantity = int(_parse_float(order.get("filled_quantity")) or 0)
                if filled_quantity <= 0:
                    continue

                filled_price = _parse_float(order.get("filled_price"))
                if filled_price is None or filled_price <= 0:
                    filled_price = _parse_float(order.get("order_price"))
                if filled_price is None or filled_price <= 0:
                    continue

                order_name = str(order.get("name") or "").strip()
                if order_name:
                    name = order_name

                if str(order.get("side") or "") == "buy":
                    order_time = str(order.get("order_time") or "").strip() or None
                    if first_buy_time is None and order_time:
                        first_buy_time = order_time
                    buy_lots.append(
                        {
                            "quantity": filled_quantity,
                            "price": filled_price,
                            "order_time": order_time,
                        }
                    )
                    continue

                remaining_sell = filled_quantity
                while remaining_sell > 0 and buy_lots:
                    lot = buy_lots[0]
                    lot_quantity = int(lot.get("quantity") or 0)
                    lot_price = _parse_float(lot.get("price")) or 0.0
                    if lot_quantity <= 0 or lot_price <= 0:
                        buy_lots.pop(0)
                        continue

                    matched = min(remaining_sell, lot_quantity)
                    matched_quantity += matched
                    matched_buy_amount += lot_price * matched
                    matched_sell_amount += filled_price * matched
                    remaining_sell -= matched
                    lot["quantity"] = lot_quantity - matched
                    last_exit_time = (
                        str(order.get("order_time") or "").strip() or last_exit_time
                    )

                    if int(lot.get("quantity") or 0) <= 0:
                        buy_lots.pop(0)

            if matched_quantity <= 0:
                continue
            if symbol in active_symbols:
                continue
            if any(int(lot.get("quantity") or 0) > 0 for lot in buy_lots):
                continue
            if matched_buy_amount <= 0:
                continue

            profit = matched_sell_amount - matched_buy_amount
            summaries.append(
                {
                    "name": name or symbol,
                    "symbol": symbol,
                    "volume": matched_quantity,
                    "buy_amount": matched_buy_amount,
                    "sell_amount": matched_sell_amount,
                    "buy_price": matched_buy_amount / matched_quantity,
                    "sell_price": matched_sell_amount / matched_quantity,
                    "profit": profit,
                    "profit_ratio": profit / matched_buy_amount,
                    "opened_at": first_buy_time,
                    "closed_at": last_exit_time,
                }
            )

        summaries.sort(
            key=lambda item: str(item.get("closed_at") or ""),
            reverse=True,
        )
        return summaries

    def _prepare_run(
        self,
        *,
        trigger_source: str,
        account_id: int,
        schedule_id: int | None,
        manual_run_type: str | None = None,
        lease_token: str | None = None,
    ) -> tuple[int, AccountRunContext]:
        """准备一次账户运行：校验账户/任务、构建上下文、创建 StrategyRun（§8.1）。"""
        with session_scope() as db:
            settings = self.get_or_create_settings(db)
            account_context = build_account_run_context(
                db,
                account_id=account_id,
                schedule_id=schedule_id,
                manual_run_type=manual_run_type,
                global_settings=settings,
            )
            run = StrategyRun(
                trading_account_id=account_context.account_id,
                trading_account_name_snapshot=account_context.account_name,
                llm_config_source=account_context.llm.source,
                llm_model_snapshot=account_context.llm.model,
                trigger_source=trigger_source,
                run_type=account_context.run_type,
                schedule_id=account_context.schedule_id,
                schedule_name=account_context.schedule_name,
                status="running",
            )
            db.add(run)
            db.flush()
            run_id = run.id
        return run_id, account_context

    def _run_body(
        self,
        *,
        run_id: int,
        account_context: AccountRunContext,
        trigger_source: str,
        schedule_id: int | None,
        emit: Any = None,
        return_full_run: bool = True,
        lease_token: str | None = None,
    ) -> StrategyRun | None:
        session_context: PersistentRunSessionContext | None = None
        automation_phase = "llm"
        _emit = emit if callable(emit) else (lambda *_a, **_kw: None)

        try:
            logger.info(
                "execute_run started: run_id=%s, account_id=%s, trigger=%s, schedule_id=%s",
                run_id,
                account_context.account_id,
                trigger_source,
                schedule_id,
            )
            _emit(
                "stage",
                stage="started",
                message="任务已启动",
                trigger_source=trigger_source,
                schedule_id=schedule_id,
                trading_account_id=account_context.account_id,
                trading_account_name=account_context.account_name,
            )

            settings = SimpleNamespace(**account_context.to_settings_snapshot())
            mx_client_config = build_skill_context(
                run_type=account_context.run_type,
                app_settings=settings,
                trading_account_id=account_context.account_id,
                trading_account_name=account_context.account_name,
                account_context=account_context,
            )["mx_client_config"]
            if not mx_client_config.get("api_key"):
                raise RuntimeError(
                    "账户未配置妙想 Key，请先在账户设置中保存后再运行。"
                )
            client = MXClient(
                api_key=account_context.mx_api_key,
                base_url=account_context.mx_api_base_url,
            )
            try:
                _emit("stage", stage="llm", message="正在调用大模型")
                session_context = self._prepare_persistent_session_context(
                    run_id=run_id,
                    settings=settings,
                    trigger_source=trigger_source,
                    schedule_id=schedule_id,
                    trading_account_id=account_context.account_id,
                )
                decision, llm_request, llm_response, runtime_trace = (
                    llm_service.run_agent_with_messages(
                        app_settings=settings,
                        client=client,
                        messages=session_context.messages,
                        emit=_emit,
                    )
                )
            finally:
                client.close()

            tool_calls = decision.get("tool_calls")
            prefetched_tool_calls = decision.get("prefetched_tool_calls")
            prefetched_freshness = decision.get("freshness")
            skill_payloads = {
                "tool_calls": tool_calls,
                "prefetched_tool_calls": prefetched_tool_calls,
                "prefetched_freshness": prefetched_freshness,
                "runtime_trace": runtime_trace,
            }
            executed_actions = self._extract_executed_actions(tool_calls)
            persisted_trade_orders = [
                {
                    "symbol": action.get("symbol"),
                    "action": action.get("action"),
                    "quantity": action.get("quantity"),
                    "price": action.get("price"),
                    "status": action.get("status") or "submitted",
                }
                for action in executed_actions
                if str(action.get("action") or "") in {"BUY", "SELL"}
            ]
            completed_at = now_utc()
            completed_at_shanghai = completed_at.astimezone(SHANGHAI_TZ)

            if persisted_trade_orders:
                _emit(
                    "stage",
                    stage="trade",
                    message=f"正在写入交易执行记录（{len(persisted_trade_orders)} 条）",
                )

            with session_scope() as db:
                run = db.get(StrategyRun, run_id)
                if run is None:
                    raise RuntimeError("运行记录不存在。")
                run.chat_session_id = session_context.session_id if session_context else None
                run.prompt_message_id = (
                    session_context.prompt_message_id if session_context else None
                )
                run.context_summary_version = (
                    session_context.summary_revision if session_context else None
                )
                run.context_tokens_estimate = (
                    session_context.context_tokens_estimate if session_context else None
                )
                run.skill_payloads = skill_payloads
                run.llm_request_payload = llm_request
                run.llm_response_payload = llm_response
                run.decision_payload = decision
                run.analysis_summary = self._build_analysis_summary(
                    decision.get("final_answer")
                )
                run.final_answer = (
                    str(decision.get("final_answer") or "").strip() or None
                )
                run.executed_actions = executed_actions
                run.status = "completed"
                run.finished_at = completed_at
                db.add(run)

                for action in executed_actions:
                    if str(action.get("action") or "") not in {"BUY", "SELL"}:
                        continue
                    db.add(
                        TradeOrder(
                            run_id=run_id,
                            symbol=str(action.get("symbol") or ""),
                            action=str(action.get("action") or ""),
                            quantity=int(action.get("quantity") or 0),
                            price_type=str(action.get("price_type") or "MARKET"),
                            price=_parse_float(action.get("price")),
                            status=str(action.get("status") or "submitted"),
                            response_payload=action.get("response"),
                        )
                    )

                if schedule_id:
                    schedule = db.get(StrategySchedule, schedule_id)
                    if schedule is not None:
                        owner_ok = _owns_schedule_lease(schedule, lease_token)
                        if owner_ok:
                            schedule.last_run_at = completed_at
                            schedule.retry_count = 0
                            schedule.retry_after_at = None
                            # 完成即释放租约（§11.5），仅持有 token 的运行可清理
                            schedule.lease_token = None
                            schedule.lease_until = None
                            schedule.next_run_at = self._compute_next_run_at(
                                schedule.cron_expression,
                                from_time=completed_at_shanghai,
                            )
                            db.add(schedule)

                if session_context is not None:
                    session = db.get(ChatSession, session_context.session_id)
                    if session is not None:
                        previous_summary_revision = int(session.summary_revision or 0)
                        assistant_content = self._build_persistent_session_assistant_content(
                            run_id=run_id,
                            run_type=str(getattr(settings, "run_type", "analysis") or "analysis"),
                            status="completed",
                            final_answer=str(decision.get("final_answer") or "").strip()
                            or None,
                            tool_calls=tool_calls if isinstance(tool_calls, list) else None,
                            executed_actions=executed_actions,
                        )
                        response_message = self._persist_persistent_session_assistant_message(
                            db=db,
                            session=session,
                            run_id=run_id,
                            content=assistant_content,
                            tool_calls=tool_calls if isinstance(tool_calls, list) else None,
                            status="completed",
                            meta_payload={
                                "run_type": str(
                                    getattr(settings, "run_type", "analysis") or "analysis"
                                ),
                                "executed_action_count": len(executed_actions),
                            },
                        )
                        session_context.response_message_id = response_message.id
                        run.response_message_id = response_message.id
                        archived_summary, summary_version = (
                            self._maybe_compact_persistent_session(
                                db=db,
                                session=session,
                                settings=settings,
                                estimated_tokens=int(
                                    session_context.context_tokens_estimate or 0
                                ),
                            )
                        )
                        if (
                            summary_version is not None
                            and int(summary_version) > previous_summary_revision
                            and str(archived_summary or "").strip()
                        ):
                            summary_message = self._persist_persistent_session_system_message(
                                db=db,
                                session=session,
                                run_id=run_id,
                                content="[上下文压缩摘要]\n" + str(archived_summary).strip(),
                                meta_payload={
                                    "summary_revision": int(summary_version),
                                    "last_compacted_run_id": session.last_compacted_run_id,
                                },
                            )
                            _emit(
                                "context_compacted",
                                message="已生成上下文压缩摘要",
                                content=summary_message.content,
                                summary_revision=int(summary_version),
                                message_id=summary_message.id,
                                run_id=run_id,
                            )
                        run.context_summary_version = (
                            int(summary_version)
                            if summary_version is not None
                            else run.context_summary_version
                        )
                        db.add(run)

            for action in persisted_trade_orders:
                _emit(
                    "trade_order",
                    symbol=action.get("symbol"),
                    action=action.get("action"),
                    quantity=action.get("quantity"),
                    price=action.get("price"),
                    status=action.get("status") or "submitted",
                )

            # --- Telegram notification for trade orders ---
            if account_context.tg_notify_trade_enabled and persisted_trade_orders:
                from app.services.notification_service import send_telegram_trade_notification
                try:
                    send_telegram_trade_notification(
                        bot_token=account_context.tg_bot_token or "",
                        chat_id=account_context.tg_chat_id or "",
                        trade_orders=[
                            action for action in executed_actions
                            if str(action.get("action") or "") in {"BUY", "SELL"}
                        ],
                        run_id=run_id,
                        trigger_source=trigger_source,
                        schedule_name=account_context.schedule_name,
                        app_display_name=account_context.account_name or "",
                    )
                except Exception:
                    logger.warning(
                        "Telegram notification failed for run_id=%s (suppressed)",
                        run_id,
                        exc_info=True,
                    )

            if not return_full_run:
                logger.info(
                    "execute_run completed: run_id=%s, actions=%d",
                    run_id,
                    len(executed_actions),
                )
                _emit(
                    "completed",
                    message="任务完成",
                    actions=len(executed_actions),
                )
                return None

            with session_scope() as db:
                run = self.get_run(db, run_id)
                if run is None:
                    raise RuntimeError("运行记录不存在。")
                logger.info(
                    "execute_run completed: run_id=%s, actions=%d",
                    run_id,
                    len(executed_actions),
                )
                _emit(
                    "completed",
                    message="任务完成",
                    actions=len(executed_actions),
                )
                return run
        except Exception as exc:
            fail_closed_error = isinstance(
                exc,
                (LLMFreshnessError, LLMMutationSafetyError),
            )
            logger.error(
                "execute_run failed: run_id=%s, error=%s",
                run_id,
                exc,
            )
            with session_scope() as db:
                run = db.get(StrategyRun, run_id)
                if run is not None:
                    run.chat_session_id = (
                        session_context.session_id if session_context else None
                    )
                    run.prompt_message_id = (
                        session_context.prompt_message_id if session_context else None
                    )
                    run.response_message_id = (
                        session_context.response_message_id if session_context else None
                    )
                    run.context_summary_version = (
                        session_context.summary_revision if session_context else None
                    )
                    run.context_tokens_estimate = (
                        session_context.context_tokens_estimate
                        if session_context
                        else None
                    )
                    run.status = "failed"
                    run.error_message = str(exc)
                    run.final_answer = None
                    run.finished_at = now_utc()
                    if hasattr(exc, "prefetched_tool_calls") or hasattr(
                        exc, "freshness"
                    ):
                        failure_skill_payloads = (
                            dict(run.skill_payloads)
                            if isinstance(run.skill_payloads, dict)
                            else {}
                        )
                        failure_skill_payloads["prefetched_tool_calls"] = getattr(
                            exc, "prefetched_tool_calls", None
                        )
                        failure_skill_payloads["prefetched_freshness"] = getattr(
                            exc, "freshness", None
                        )
                        run.skill_payloads = failure_skill_payloads
                    db.add(run)
                    if session_context is not None:
                        session = db.get(ChatSession, session_context.session_id)
                        if session is not None:
                            assistant_content = self._build_persistent_session_assistant_content(
                                run_id=run_id,
                                run_type=account_context.run_type,
                                status="failed",
                                final_answer=None,
                                tool_calls=None,
                                executed_actions=None,
                                error_message=str(exc),
                                phase=automation_phase,
                            )
                            response_message = self._persist_persistent_session_assistant_message(
                                db=db,
                                session=session,
                                run_id=run_id,
                                content=assistant_content,
                                tool_calls=None,
                                status="failed",
                                meta_payload={
                                    "phase": automation_phase,
                                    "run_type": str(
                                        account_context.run_type
                                    ),
                                },
                            )
                            run.response_message_id = response_message.id
                            session_context.response_message_id = response_message.id
                            db.add(run)
                if schedule_id:
                    schedule = db.get(StrategySchedule, schedule_id)
                    if schedule is not None and _owns_schedule_lease(
                        schedule, lease_token
                    ):
                        schedule.last_run_at = now_utc()
                        # 失败也要释放租约（§11.5），重试由 retry_after_at 机制驱动
                        schedule.lease_token = None
                        schedule.lease_until = None
                        schedule.next_run_at = self._compute_next_run_at(
                            schedule.cron_expression,
                            from_time=now_shanghai(),
                        )
                        if trigger_source == "schedule":
                            retry_count = max(int(schedule.retry_count or 0), 0)
                            if fail_closed_error:
                                schedule.retry_count = retry_count
                                schedule.retry_after_at = None
                                logger.warning(
                                    "schedule run fail-closed without retry: "
                                    "run_id=%s, schedule_id=%s, error_type=%s",
                                    run_id,
                                    schedule_id,
                                    type(exc).__name__,
                                )
                            elif retry_count < SCHEDULE_MAX_RETRIES:
                                schedule.retry_count = retry_count + 1
                                schedule.retry_after_at = now_utc() + SCHEDULE_RETRY_DELAY
                            else:
                                schedule.retry_count = 0
                                schedule.retry_after_at = None
                        else:
                            schedule.retry_count = max(int(schedule.retry_count or 0), 0)
                        db.add(schedule)
            _emit("failed", message=str(exc))
            raise

    def execute_run(
        self,
        *,
        account_id: int | None = None,
        trigger_source: str = "manual",
        schedule_id: int | None = None,
        manual_run_type: str | None = None,
        lease_token: str | None = None,
    ) -> StrategyRun:
        """账户级串行执行（§11.1）：同一账户互斥，不同账户可并发。"""
        with session_scope() as db:
            resolved_account_id = self._resolve_account_id(db, account_id)
        lock = self._get_account_run_lock(resolved_account_id)
        if not lock.acquire(blocking=False):
            raise RuntimeError("该账户已有任务正在运行，请稍后再试。")
        try:
            run_id, account_context = self._prepare_run(
                trigger_source=trigger_source,
                account_id=resolved_account_id,
                schedule_id=schedule_id,
                manual_run_type=manual_run_type,
                lease_token=lease_token,
            )
            return self._run_body(
                run_id=run_id,
                account_context=account_context,
                trigger_source=trigger_source,
                schedule_id=schedule_id,
                lease_token=lease_token,
            )
        finally:
            lock.release()

    def start_run_async(
        self,
        *,
        account_id: int | None = None,
        trigger_source: str = "manual",
        schedule_id: int | None = None,
        manual_run_type: str | None = None,
        lease_token: str | None = None,
    ) -> int:
        """Launch a run on a background thread and return its run_id immediately.

        Event stream: subscribe via ``event_bus`` using the returned run_id.
        """
        with session_scope() as db:
            resolved_account_id = self._resolve_account_id(db, account_id)
        lock = self._get_account_run_lock(resolved_account_id)
        if not lock.acquire(blocking=False):
            raise RuntimeError("该账户已有任务正在运行，请稍后再试。")

        run_id: int | None = None
        try:
            run_id, account_context = self._prepare_run(
                trigger_source=trigger_source,
                account_id=resolved_account_id,
                schedule_id=schedule_id,
                manual_run_type=manual_run_type,
                lease_token=lease_token,
            )
        except Exception:
            lock.release()
            raise

        emit = make_emitter(run_id)

        def _worker() -> None:
            try:
                self._run_body(
                    run_id=run_id,
                    account_context=account_context,
                    trigger_source=trigger_source,
                    schedule_id=schedule_id,
                    emit=emit,
                    return_full_run=False,
                    lease_token=lease_token,
                )
            except Exception:
                logger.exception("async run worker failed: run_id=%s", run_id)
            finally:
                lock.release()

        Thread(
            target=_worker,
            name=f"run-{run_id}",
            daemon=True,
        ).start()
        return run_id

    def process_due_schedule(self) -> None:
        """账户级调度（§11.4）：lease 原子抢占，按账户分组并发派发。"""
        now_sh = now_shanghai()
        now = (
            now_sh.astimezone(timezone.utc)
            if now_sh.tzinfo is not None
            else now_sh.replace(tzinfo=timezone.utc)
        )
        # SQLite 存储为 naive UTC 字符串，SQL 比较统一绑定 naive UTC。
        now_naive = now.replace(tzinfo=None)

        from sqlalchemy import update as sa_update

        def _clear_lease(db: Session, schedule_id: int) -> None:
            schedule = db.get(StrategySchedule, schedule_id)
            if schedule is not None:
                schedule.lease_token = None
                schedule.lease_until = None
                db.add(schedule)

        with session_scope() as db:
            schedules = db.scalars(
                select(StrategySchedule).where(StrategySchedule.enabled.is_(True))
            ).all()

            if not trading_calendar_service.is_trading_day(now_sh.date()):
                for schedule in schedules:
                    schedule.next_run_at = self._compute_next_run_at(
                        schedule.cron_expression,
                        from_time=now_sh,
                    )
                    db.add(schedule)
                return

            due: list[StrategySchedule] = []
            for schedule in schedules:
                if schedule.next_run_at is None:
                    schedule.next_run_at = self._compute_next_run_at(
                        schedule.cron_expression
                    )
                    db.add(schedule)
                    continue
                next_run_at = _assume_utc(schedule.next_run_at)
                retry_after_at = _assume_utc(schedule.retry_after_at)
                if next_run_at <= now or (
                    retry_after_at is not None and retry_after_at <= now
                ):
                    due.append(schedule)

            claimed_ids: list[tuple[int, int, str]] = []
            for schedule in due:
                # 原子抢占：受影响行数为 1 表示抢占成功，0 表示跳过。
                # 每个任务独立 token，运行完成/失败时按 token 校验所有权后再清理。
                claim_token = secrets.token_hex(16)
                result = db.execute(
                    sa_update(StrategySchedule)
                    .where(
                        StrategySchedule.id == schedule.id,
                        StrategySchedule.enabled.is_(True),
                        (
                            (StrategySchedule.lease_until.is_(None))
                            | (StrategySchedule.lease_until < now_naive)
                        ),
                        (
                            (StrategySchedule.retry_after_at.is_(None))
                            | (StrategySchedule.retry_after_at <= now_naive)
                        ),
                        (
                            (StrategySchedule.next_run_at <= now_naive)
                            | (StrategySchedule.retry_after_at <= now_naive)
                        ),
                    )
                    .values(
                        lease_token=claim_token,
                        lease_until=now_naive
                        + timedelta(seconds=SCHEDULE_LEASE_SECONDS),
                    )
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount == 1:
                    claimed_ids.append(
                        (schedule.id, int(schedule.trading_account_id or 0), claim_token)
                    )
            db.commit()

            if not claimed_ids:
                return

            # 同一账户同一轮只派发一个任务；不同账户通过后台线程并发执行。
            dispatched_accounts: set[int] = set()
            for schedule_id, account_id, claim_token in claimed_ids:
                if account_id in dispatched_accounts:
                    with session_scope() as db2:
                        _clear_lease(db2, schedule_id)
                    continue
                if account_id <= 0:
                    # 旧库/直插的 NULL 归属任务：回填默认账户。
                    with session_scope() as db2:
                        account = account_service.resolve_single_active_account(db2)
                        if account is None:
                            _clear_lease(db2, schedule_id)
                            continue
                        account_id = account.id
                        schedule = db2.get(StrategySchedule, schedule_id)
                        if schedule is not None:
                            schedule.trading_account_id = account_id
                            db2.add(schedule)
                # 停用/归档账户的到期任务：释放租约并推进 next_run_at，避免每轮重复尝试。
                with session_scope() as db2:
                    account = db2.get(TradingAccount, account_id)
                    if account is None or not account.enabled or account.archived:
                        schedule = db2.get(StrategySchedule, schedule_id)
                        if schedule is not None:
                            schedule.lease_token = None
                            schedule.lease_until = None
                            schedule.next_run_at = self._compute_next_run_at(
                                schedule.cron_expression,
                                from_time=now_sh,
                            )
                            db2.add(schedule)
                        logger.info(
                            "process_due_schedule skipped (account disabled/archived): "
                            "schedule_id=%s account_id=%s",
                            schedule_id,
                            account_id,
                        )
                        continue
                dispatched_accounts.add(account_id)
                try:
                    self.start_run_async(
                        account_id=account_id,
                        trigger_source="schedule",
                        schedule_id=schedule_id,
                        lease_token=claim_token,
                    )
                except RuntimeError as exc:
                    if "已有任务正在运行" in str(exc):
                        logger.info(
                            "process_due_schedule skipped (account busy): schedule_id=%s account_id=%s",
                            schedule_id,
                            account_id,
                        )
                        # 账户锁竞争失败：保留任务待下一轮。
                        with session_scope() as db2:
                            _clear_lease(db2, schedule_id)
                        continue
                    logger.exception(
                        "process_due_schedule run launch failed: schedule_id=%s account_id=%s",
                        schedule_id,
                        account_id,
                    )
                    with session_scope() as db2:
                        _clear_lease(db2, schedule_id)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "process_due_schedule run launch failed: schedule_id=%s account_id=%s",
                        schedule_id,
                        account_id,
                    )
                    with session_scope() as db2:
                        _clear_lease(db2, schedule_id)

    def _safe_call(self, func: Any) -> dict[str, Any]:
        try:
            return {"ok": True, "result": func()}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _get_recent_account_snapshot(
        self, db: Session, account_id: int | None = None
    ) -> tuple[
        dict[str, Any] | None,
        dict[str, Any] | None,
        dict[str, Any] | None,
    ]:
        resolved_account_id = self._resolve_account_id(db, account_id)
        stmt = (
            select(StrategyRun)
            .where(StrategyRun.trading_account_id == resolved_account_id)
            .order_by(StrategyRun.started_at.desc())
            .limit(20)
        )

        balance_result: dict[str, Any] | None = None
        positions_result: dict[str, Any] | None = None
        orders_result: dict[str, Any] | None = None

        for run in db.scalars(stmt).all():
            tool_calls = self._get_run_tool_calls(run)
            if not tool_calls:
                continue

            if balance_result is None:
                balance_result = self._extract_tool_result(tool_calls, "mx_get_balance")
            if positions_result is None:
                positions_result = self._extract_tool_result(
                    tool_calls, "mx_get_positions"
                )
            if orders_result is None:
                orders_result = self._extract_tool_result(tool_calls, "mx_get_orders")

            if (
                balance_result is not None
                and positions_result is not None
                and orders_result is not None
            ):
                break

        return balance_result, positions_result, orders_result

    def _get_run_tool_calls(self, run: StrategyRun) -> list[dict[str, Any]]:
        skill_payloads = (
            run.skill_payloads if isinstance(run.skill_payloads, dict) else {}
        )
        decision_payload = (
            run.decision_payload if isinstance(run.decision_payload, dict) else {}
        )

        combined_tool_calls: list[dict[str, Any]] = []
        prefetched_tool_calls = skill_payloads.get("prefetched_tool_calls")
        if isinstance(prefetched_tool_calls, list):
            combined_tool_calls.extend(
                item for item in prefetched_tool_calls if isinstance(item, dict)
            )

        tool_calls = self._get_detail_tool_calls(run)
        if tool_calls:
            combined_tool_calls.extend(
                tool_calls
            )
        return combined_tool_calls

    def _extract_executed_actions(self, tool_calls: Any) -> list[dict[str, Any]]:
        if not isinstance(tool_calls, list):
            return []

        executed_actions: list[dict[str, Any]] = []
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            result = item.get("result")
            if not isinstance(result, dict) or not result.get("ok"):
                continue
            executed_action = result.get("executed_action")
            if not isinstance(executed_action, dict):
                continue
            action_name = str(executed_action.get("action") or "").upper()
            entry = {
                "symbol": str(
                    executed_action.get("symbol")
                    or executed_action.get("stock_code")
                    or ""
                ).strip(),
                "name": str(executed_action.get("name") or "").strip() or None,
                "action": action_name,
                "quantity": int(executed_action.get("quantity") or 0),
                "price_type": str(executed_action.get("price_type") or "MARKET"),
                "price": executed_action.get("price"),
                "reason": str(executed_action.get("reason") or "").strip(),
                "position_pct": executed_action.get("position_pct"),
                "status": "submitted",
                "response": result.get("result"),
            }
            if action_name == "CANCEL":
                entry["price_type"] = "CANCEL"
                entry["status"] = "cancel_requested"
            if action_name == "MANAGE_SELF_SELECT":
                entry["price_type"] = "SELF_SELECT"
                entry["status"] = "completed"
                entry["symbol"] = str(executed_action.get("query") or "")
            executed_actions.append(entry)
        return executed_actions

    def _build_analysis_summary(self, final_answer: Any) -> str | None:
        text = str(final_answer or "").strip()
        if not text:
            return None
        compact = " ".join(text.split())
        if len(compact) <= 120:
            return compact
        return compact[:117] + "..."

    def _prepare_persistent_session_context(
        self,
        *,
        run_id: int,
        settings: Any,
        trigger_source: str,
        schedule_id: int | None,
        trading_account_id: int | None = None,
    ) -> PersistentRunSessionContext:
        resolved_account_id = trading_account_id or int(
            getattr(settings, "account_id", 0) or 0
        )
        with session_scope() as db:
            if resolved_account_id <= 0:
                resolved_account_id = self._resolve_account_id(db, None)
            session = self._get_or_create_persistent_session(
                db, trading_account_id=resolved_account_id
            )
            user_content = self._build_persistent_session_user_content(
                settings=settings,
                trigger_source=trigger_source,
                schedule_id=schedule_id,
                schedule_name=getattr(settings, "schedule_name", None),
                run_type=str(getattr(settings, "run_type", "analysis") or "analysis"),
                task_prompt=str(getattr(settings, "task_prompt", "") or ""),
                prefetched_context=None,
            )
            user_message = self._persist_persistent_session_user_message(
                db=db,
                session=session,
                run_id=run_id,
                content=user_content,
                schedule_id=schedule_id,
                schedule_name=getattr(settings, "schedule_name", None),
                run_type=str(getattr(settings, "run_type", "analysis") or "analysis"),
                trigger_source=trigger_source,
            )
            history_records = self._list_persistent_session_history_records(
                db=db,
                session_id=session.id,
                recent_limit=int(
                    getattr(settings, "automation_recent_message_limit", 0)
                    or AUTOMATION_DEFAULT_RECENT_MESSAGE_LIMIT
                ),
            )
            history_messages = self._build_persistent_session_history_messages(
                history_records
            )
            messages = self._build_persistent_session_prompt_messages(
                session=session,
                history_messages=history_messages,
                memory_messages=self._retrieve_persistent_session_memory_messages(
                    session=session,
                    settings=settings,
                    run_type=str(getattr(settings, "run_type", "analysis") or "analysis"),
                    task_prompt=str(getattr(settings, "task_prompt", "") or ""),
                ),
            )
            context_tokens_estimate = self._estimate_persistent_session_context_tokens(
                session=session,
                settings=settings,
                messages=messages,
            )
            context_tokens_estimate = max(
                context_tokens_estimate,
                estimate_messages_tokens(history_messages),
            )
            with db.no_autoflush:
                run = db.get(StrategyRun, run_id)
                if run is not None:
                    run.chat_session_id = session.id
                    run.prompt_message_id = user_message.id
                    run.context_tokens_estimate = context_tokens_estimate
                    run.context_summary_version = int(session.summary_revision or 0)
                    db.add(run)

            return PersistentRunSessionContext(
                session_id=session.id,
                prompt_message_id=user_message.id,
                response_message_id=None,
                summary_revision=int(session.summary_revision or 0),
                context_tokens_estimate=context_tokens_estimate,
                messages=messages,
            )

    def _get_or_create_persistent_session(
        self, db: Session, *, trading_account_id: int
    ) -> ChatSession:
        """按账户获取/创建自动化会话（slug=automation-{account_id}，§8.4）。"""
        account = db.get(TradingAccount, trading_account_id)
        if account is None:
            raise RuntimeError("交易账户不存在。")
        previous_session_id = int(getattr(account, "automation_session_id", 0) or 0)
        session = get_or_create_automation_session(
            db,
            account_id=trading_account_id,
            previous_session_id=previous_session_id,
        )
        account.automation_session_id = session.id
        db.add(account)
        return session

    def _build_persistent_session_user_content(
        self,
        *,
        settings: Any,
        trigger_source: str,
        schedule_id: int | None,
        schedule_name: str | None,
        run_type: str,
        task_prompt: str,
        prefetched_context: str | None,
    ) -> str:
        current_time = now_shanghai()
        run_time = (
            f"{current_time.year}年{current_time.month}月{current_time.day}日 "
            f"{current_time.strftime('%H:%M:%S')}"
        )
        trigger_source_text = (
            "定时触发" if str(trigger_source or "").strip() == "schedule" else "手动触发"
        )
        task_type_text = (
            "交易任务" if str(run_type or "").strip() == "trade" else "分析任务"
        )
        lines = [
            f"时间：{run_time}",
            f"来源: {trigger_source_text}",
            f"任务类型: {task_type_text}",
            "",
            (
                "上下文边界：历史只用于策略连续性；历史行情、资讯、持仓、资金、"
                "委托全部过期，本轮必须依据[本轮实时数据快照]或本轮成功查询。"
            ),
            "",
            "本轮任务:",
            str(task_prompt or "").strip() or "--",
        ]
        del settings, schedule_name, prefetched_context
        return "\n".join(lines).strip()

    def _build_persistent_session_assistant_content(
        self,
        *,
        run_id: int,
        run_type: str,
        status: str,
        final_answer: str | None,
        tool_calls: list[dict[str, Any]] | None,
        executed_actions: list[dict[str, Any]] | None,
        error_message: str | None = None,
        phase: str | None = None,
    ) -> str:
        content = str(final_answer or "").strip()

        if status == "completed":
            del executed_actions, tool_calls, run_id, run_type
            return content or "本轮已完成，但未生成额外说明。"

        del executed_actions, tool_calls, run_id, run_type, phase
        return f"执行失败：{str(error_message or '未知错误').strip() or '未知错误'}"

    def _persist_persistent_session_user_message(
        self,
        *,
        db: Session,
        session: ChatSession,
        run_id: int,
        content: str,
        schedule_id: int | None,
        schedule_name: str | None,
        run_type: str,
        trigger_source: str,
    ) -> ChatMessageRecord:
        record = ChatMessageRecord(
            session_id=session.id,
            role="user",
            content=content,
            source="automation_run",
            run_id=run_id,
            message_kind="live_turn",
            meta_payload={
                "trigger_source": trigger_source,
                "schedule_id": schedule_id,
                "schedule_name": schedule_name,
                "run_type": run_type,
            },
        )
        db.add(record)
        db.flush()
        session.last_message_at = now_utc()
        db.add(session)
        return record

    def _slim_automation_tool_calls(
        self, tool_calls: list[dict[str, Any]] | None
    ) -> list[dict[str, Any]] | None:
        if not isinstance(tool_calls, list):
            return None
        slimmed: list[dict[str, Any]] = []
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            entry = {
                "name": item.get("name"),
                "tool_call_id": item.get("id") or item.get("tool_call_id"),
                "arguments": item.get("arguments"),
                "ok": result.get("ok"),
                "summary": result.get("summary") or result.get("error"),
            }
            executed_action = result.get("executed_action")
            if isinstance(executed_action, dict):
                entry["executed_action"] = executed_action
            slimmed.append(entry)
        return slimmed or None

    def _persist_persistent_session_assistant_message(
        self,
        *,
        db: Session,
        session: ChatSession,
        run_id: int,
        content: str,
        tool_calls: list[dict[str, Any]] | None,
        status: str,
        meta_payload: dict[str, Any] | None,
    ) -> ChatMessageRecord:
        record = ChatMessageRecord(
            session_id=session.id,
            role="assistant",
            content=content,
            source="automation_run",
            run_id=run_id,
            message_kind="live_turn",
            tool_calls=self._slim_automation_tool_calls(tool_calls),
            meta_payload={"status": status, **(meta_payload or {})} or None,
        )
        db.add(record)
        db.flush()
        session.last_message_at = now_utc()
        db.add(session)
        return record

    def _persist_persistent_session_system_message(
        self,
        *,
        db: Session,
        session: ChatSession,
        run_id: int,
        content: str,
        meta_payload: dict[str, Any] | None,
    ) -> ChatMessageRecord:
        record = ChatMessageRecord(
            session_id=session.id,
            role="system",
            content=content,
            source="automation_run",
            run_id=run_id,
            message_kind="context_compaction",
            meta_payload=meta_payload or None,
        )
        db.add(record)
        db.flush()
        session.last_message_at = now_utc()
        db.add(session)
        return record

    def _list_persistent_session_history_records(
        self,
        *,
        db: Session,
        session_id: int,
        recent_limit: int,
    ) -> list[ChatMessageRecord]:
        limit = max(4, int(recent_limit or AUTOMATION_DEFAULT_RECENT_MESSAGE_LIMIT))
        session = db.get(ChatSession, session_id)
        last_compacted_message_id = int(
            getattr(session, "last_compacted_message_id", 0) or 0
        )
        stmt = (
            select(ChatMessageRecord)
            .where(ChatMessageRecord.session_id == session_id)
            .order_by(ChatMessageRecord.id.desc())
            .limit(limit)
        )
        if last_compacted_message_id > 0:
            stmt = stmt.where(ChatMessageRecord.id > last_compacted_message_id)
        records = (
            db.execute(
                stmt
            )
            .scalars()
            .all()
        )
        records.reverse()
        return records

    def _build_persistent_session_history_messages(
        self, records: list[ChatMessageRecord]
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for record in records:
            if str(record.message_kind or "").strip() == "context_compaction":
                continue
            if record.role not in {"user", "assistant", "system"}:
                continue
            content = str(record.content or "").strip()
            if not content:
                continue
            messages.append({"role": record.role, "content": content})
        return messages

    def _retrieve_persistent_session_memory_messages(
        self,
        *,
        session: ChatSession,
        settings: Any,
        run_type: str,
        task_prompt: str,
    ) -> list[dict[str, Any]]:
        # Placeholder hook for future vector retrieval. Keep the contract stable
        # so long-term memory can be injected without reshaping the main run flow.
        del session, settings, run_type, task_prompt
        return []

    def _estimate_persistent_session_context_tokens(
        self,
        *,
        session: ChatSession,
        settings: Any,
        messages: list[dict[str, Any]],
    ) -> int:
        estimate = estimate_messages_tokens(messages)
        estimate += estimate_text_tokens(getattr(settings, "system_prompt", None))
        return estimate

    def _list_uncompacted_persistent_session_records(
        self,
        *,
        db: Session,
        session: ChatSession,
    ) -> list[ChatMessageRecord]:
        stmt = (
            select(ChatMessageRecord)
            .where(ChatMessageRecord.session_id == session.id)
            .order_by(ChatMessageRecord.id.asc())
        )
        last_compacted_message_id = int(
            getattr(session, "last_compacted_message_id", 0) or 0
        )
        if last_compacted_message_id > 0:
            stmt = stmt.where(ChatMessageRecord.id > last_compacted_message_id)
        return db.execute(stmt).scalars().all()

    def _build_persistent_session_context_system_message(
        self,
        *,
        session: ChatSession,
    ) -> dict[str, Any] | None:
        archived_summary = str(session.archived_summary or "").strip()
        if not archived_summary:
            return None
        return {
            "role": "system",
            "content": (
                "[上下文压缩摘要（历史策略记忆，行情/资讯/持仓/资金/委托均已过期，"
                "不是本轮事实）]\n"
                + archived_summary
            ),
        }

    def _build_persistent_session_prompt_messages(
        self,
        *,
        session: ChatSession,
        history_messages: list[dict[str, Any]],
        memory_messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        context_message = self._build_persistent_session_context_system_message(
            session=session,
        )
        if context_message is not None:
            messages.append(context_message)
        messages.extend(memory_messages)
        messages.extend(history_messages)
        return messages

    def _build_compacted_summary_text(
        self, records: list[ChatMessageRecord]
    ) -> str | None:
        if not records:
            return None
        assistant_records = [record for record in records if record.role == "assistant"]
        if not assistant_records:
            return None
        recent = assistant_records[-6:]
        lines = [
            "## 历史策略记忆（不是本轮事实）",
            "- 仅用于延续最近自动化运行的策略、结论和失败记录。",
            "## 已执行动作",
        ]
        for record in recent:
            summary = self._build_analysis_summary(record.content)
            run_id = record.run_id if record.run_id is not None else "--"
            if summary:
                lines.append(f"- run_id {run_id}: {summary}")
        lines.extend(
            [
                "## 当前约束",
                "- 原始运行记录和交易记录以 StrategyRun / TradeOrder 为准。",
                "- 历史行情、资讯、持仓、资金和委托均不是本轮实时事实。",
                "- 本轮账户数字必须以实时快照或本轮成功查询结果为准。",
                "## 后续计划",
                "- 下一轮获取实时证据后，再延续、调整或推翻历史计划。",
            ]
        )
        return "\n".join(lines)

    def _safe_prompt_budget(self, settings: Any) -> int:
        context_window = int(
            getattr(settings, "automation_context_window_tokens", 0)
            or AUTOMATION_DEFAULT_CONTEXT_WINDOW_TOKENS
        )
        return max(2048, int(context_window * AUTOMATION_COMPACTION_TRIGGER_RATIO))

    def _should_compact_automation_session(
        self,
        *,
        session: ChatSession,
        records: list[ChatMessageRecord],
        settings: Any,
        estimated_tokens: int,
    ) -> bool:
        if not bool(getattr(settings, "automation_enable_auto_compaction", True)):
            return False
        recent_limit = int(
            getattr(settings, "automation_recent_message_limit", 0)
            or AUTOMATION_DEFAULT_RECENT_MESSAGE_LIMIT
        )
        if len(records) > recent_limit:
            return True
        if estimated_tokens > self._safe_prompt_budget(settings):
            return True
        last_message_at = _assume_utc(session.last_message_at)
        idle_hours = int(
            getattr(settings, "automation_idle_summary_hours", 0)
            or AUTOMATION_DEFAULT_IDLE_SUMMARY_HOURS
        )
        if last_message_at is not None and idle_hours > 0:
            if now_utc() - last_message_at >= timedelta(hours=idle_hours):
                return True
        return False

    def _maybe_compact_persistent_session(
        self,
        *,
        db: Session,
        session: ChatSession,
        settings: Any,
        estimated_tokens: int,
    ) -> tuple[str | None, int | None]:
        records = self._list_uncompacted_persistent_session_records(
            db=db,
            session=session,
        )
        if not self._should_compact_automation_session(
            session=session,
            records=records,
            settings=settings,
            estimated_tokens=estimated_tokens,
        ):
            return session.archived_summary, session.summary_revision

        recent_limit = max(
            8,
            int(
                getattr(settings, "automation_recent_message_limit", 0)
                or AUTOMATION_DEFAULT_RECENT_MESSAGE_LIMIT
            )
            // 2,
        )
        compact_cutoff = max(0, len(records) - recent_limit)
        compact_candidates = records[:compact_cutoff]
        if len(compact_candidates) < 2:
            return session.archived_summary, session.summary_revision
        if len(compact_candidates) % 2 == 1:
            compact_candidates = compact_candidates[:-1]
        if len(compact_candidates) < 2:
            return session.archived_summary, session.summary_revision

        summary = self._build_compacted_summary_text(compact_candidates)
        if not summary:
            return session.archived_summary, session.summary_revision

        session.archived_summary = summary
        session.summary_updated_at = now_utc()
        session.last_compacted_message_id = compact_candidates[-1].id
        last_run_id = compact_candidates[-1].run_id
        session.last_compacted_run_id = int(last_run_id) if last_run_id else None
        session.summary_revision = int(session.summary_revision or 0) + 1
        db.add(session)
        return session.archived_summary, session.summary_revision

    def _compute_next_run_at(
        self,
        cron_expression: str | None,
        from_time: datetime | None = None,
    ) -> datetime | None:
        if not cron_expression:
            return None

        parts = cron_expression.strip().split()
        if len(parts) != 5:
            return None

        minute_expr, hour_expr, day_of_month_expr, month_expr, day_of_week_expr = parts
        try:
            minute_values = self._parse_cron_values(minute_expr, 0, 59)
            hour_values = self._parse_cron_values(hour_expr, 0, 23)
            day_of_month_values = self._parse_cron_values(day_of_month_expr, 1, 31)
            month_values = self._parse_cron_values(month_expr, 1, 12)
            day_of_week_values = self._parse_cron_values(
                day_of_week_expr,
                0,
                6,
                allow_seven_as_zero=True,
            )
        except ValueError:
            return None

        current_base = from_time or now_shanghai()
        if current_base.tzinfo is None:
            current_base = current_base.replace(tzinfo=SHANGHAI_TZ)
        else:
            current_base = current_base.astimezone(SHANGHAI_TZ)

        current = current_base.replace(second=0, microsecond=0) + timedelta(minutes=1)

        for _ in range(60 * 24 * 366 * 2):
            if not trading_calendar_service.is_trading_day(current.date()):
                next_day = trading_calendar_service.next_trading_day(current.date())
                current = datetime.combine(
                    next_day, datetime.min.time(), tzinfo=SHANGHAI_TZ
                )
                continue

            if (
                current.minute in minute_values
                and current.hour in hour_values
                and current.month in month_values
                and self._matches_cron_day(
                    current,
                    day_of_month_values=day_of_month_values,
                    day_of_week_values=day_of_week_values,
                    day_of_month_expr=day_of_month_expr,
                    day_of_week_expr=day_of_week_expr,
                )
            ):
                return current.astimezone(timezone.utc)
            current += timedelta(minutes=1)
        return None

    def _parse_cron_values(
        self,
        expression: str,
        minimum: int,
        maximum: int,
        *,
        allow_seven_as_zero: bool = False,
    ) -> set[int]:
        expr = expression.strip()
        allowed: set[int] = set()
        for part in expr.split(","):
            part = part.strip()
            if not part:
                raise ValueError("invalid cron expression")

            range_part = part
            step = 1
            if "/" in part:
                range_part, step_text = part.split("/", 1)
                step = int(step_text)
                if step <= 0:
                    raise ValueError("invalid cron step")

            if range_part == "*":
                start = minimum
                end = maximum
            elif "-" in range_part:
                start_text, end_text = range_part.split("-", 1)
                start = self._normalize_cron_value(
                    int(start_text),
                    minimum=minimum,
                    maximum=maximum,
                    allow_seven_as_zero=allow_seven_as_zero,
                )
                end = self._normalize_cron_value(
                    int(end_text),
                    minimum=minimum,
                    maximum=maximum,
                    allow_seven_as_zero=allow_seven_as_zero,
                )
                if start > end:
                    raise ValueError("invalid cron range")
            else:
                numeric = self._normalize_cron_value(
                    int(range_part),
                    minimum=minimum,
                    maximum=maximum,
                    allow_seven_as_zero=allow_seven_as_zero,
                )
                start = numeric
                end = numeric

            allowed.update(range(start, end + 1, step))

        return allowed

    def _normalize_cron_value(
        self,
        value: int,
        *,
        minimum: int,
        maximum: int,
        allow_seven_as_zero: bool = False,
    ) -> int:
        if allow_seven_as_zero and value == 7:
            value = 0
        if value < minimum or value > maximum:
            raise ValueError("cron value out of range")
        return value

    def _matches_cron_day(
        self,
        current: datetime,
        *,
        day_of_month_values: set[int],
        day_of_week_values: set[int],
        day_of_month_expr: str,
        day_of_week_expr: str,
    ) -> bool:
        day_of_month_matches = current.day in day_of_month_values
        current_day_of_week = (current.weekday() + 1) % 7
        day_of_week_matches = current_day_of_week in day_of_week_values

        day_of_month_is_wildcard = day_of_month_expr.strip() == "*"
        day_of_week_is_wildcard = day_of_week_expr.strip() == "*"

        if day_of_month_is_wildcard and day_of_week_is_wildcard:
            return True
        if day_of_month_is_wildcard:
            return day_of_week_matches
        if day_of_week_is_wildcard:
            return day_of_month_matches
        return day_of_month_matches or day_of_week_matches

    def _build_account_overview(
        self,
        balance_payload: dict[str, Any] | None,
        positions_payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        balance = (
            balance_payload.get("data") if isinstance(balance_payload, dict) else {}
        )
        positions_source = (
            positions_payload.get("data") if isinstance(positions_payload, dict) else []
        )
        if isinstance(positions_source, dict):
            rows = (
                positions_source.get("data")
                or positions_source.get("rows")
                or positions_source.get("list")
                or positions_source.get("posList")
                or []
            )
        else:
            rows = positions_source or []

        total_assets = None
        total_market_value = None
        holding_profit = None
        daily_profit = None
        daily_profit_trade_date = None
        open_date = None
        operating_days = None
        initial_capital = None
        cash_balance = None
        total_position_ratio = None
        nav = None
        if isinstance(balance, dict):
            open_date = _format_open_date(balance.get("openDate"))
            operating_days = int(_parse_float(balance.get("oprDays")) or 0) or None
            initial_capital = _parse_float(balance.get("initMoney"))
            total_assets = _parse_float(
                balance.get("totalAsset")
                or balance.get("totalAssets")
                or balance.get("asset")
                or balance.get("totalMoney")
                or (
                    (balance.get("result") or {}).get("totalAssets")
                    if isinstance(balance.get("result"), dict)
                    else None
                )
            )
            total_market_value = _parse_float(
                balance.get("marketValue")
                or balance.get("stockMarketValue")
                or balance.get("positionValue")
                or balance.get("totalPosValue")
            )
            cash_balance = _parse_float(
                balance.get("balanceActual")
                or balance.get("availBalance")
                or balance.get("cashBalance")
            )
            total_position_ratio = _normalize_percent(
                _parse_float(balance.get("totalPosPct"))
            )
            holding_profit = _parse_float(
                balance.get("holdingProfit")
                or balance.get("positionProfit")
                or balance.get("floatProfit")
                or balance.get("totalProfit")
            )
            nav = _parse_float(balance.get("nav"))
            daily_profit = _parse_float(
                balance.get("todayProfit")
                or balance.get("dailyProfit")
                or balance.get("profitToday")
            )
            raw_trade_date = (
                balance.get("tradeDate")
                or balance.get("tradingDate")
                or balance.get("date")
                or balance.get("profitDate")
            )
            if raw_trade_date:
                text = str(raw_trade_date).strip()
                if len(text) == 8 and text.isdigit():
                    daily_profit_trade_date = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
                elif len(text) >= 10:
                    daily_profit_trade_date = text[:10]

        if holding_profit is None and isinstance(positions_source, dict):
            holding_profit = _parse_float(positions_source.get("totalProfit"))

        if daily_profit is None:
            daily_profit = sum(
                _parse_float(row.get("dayProfit")) or 0.0
                for row in rows
                if isinstance(row, dict)
            )

        total_return_ratio = None
        if nav is not None:
            total_return_ratio = nav - 1
        elif total_assets is not None and initial_capital not in (None, 0):
            total_return_ratio = total_assets / initial_capital - 1

        daily_return_ratio = None
        if daily_profit is not None and total_assets is not None:
            previous_assets = total_assets - daily_profit
            if previous_assets > 0:
                daily_return_ratio = daily_profit / previous_assets

        if daily_profit_trade_date is None:
            today = now_shanghai().date()
            if trading_calendar_service.is_trading_day(today):
                daily_profit_trade_date = today.isoformat()
            else:
                probe = today - timedelta(days=1)
                for _ in range(30):
                    if trading_calendar_service.is_trading_day(probe):
                        break
                    probe -= timedelta(days=1)
                daily_profit_trade_date = probe.isoformat()

        normalized_positions: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            amount = (
                _parse_float(
                    row.get("marketValue")
                    or row.get("market_amount")
                    or row.get("amount")
                    or row.get("positionValue")
                    or row.get("value")
                )
                or 0.0
            )
            profit_value = _parse_float(
                row.get("profitRatio")
                or row.get("profit_rate")
                or row.get("yieldRate")
                or row.get("profitPercent")
                or row.get("profitPct")
            )
            profit_ratio = _normalize_percent(profit_value)
            day_profit_ratio = _normalize_percent(_parse_float(row.get("dayProfitPct")))
            position_ratio = None
            if total_assets and total_assets > 0:
                position_ratio = max(0.0, min(1.0, amount / total_assets))
            if position_ratio is None:
                position_ratio = _normalize_percent(_parse_float(row.get("posPct")))

            raw_symbol = str(
                row.get("stockCode")
                or row.get("code")
                or row.get("SECURITY_CODE")
                or row.get("secCode")
                or ""
            ).strip()
            market_code = row.get("secMkt")
            if market_code is None:
                market_code = row.get("market")
            suffix = _market_suffix(market_code)
            symbol = f"{raw_symbol}.{suffix}" if raw_symbol and suffix else raw_symbol

            normalized_positions.append(
                {
                    "name": str(
                        row.get("stockName")
                        or row.get("name")
                        or row.get("SECURITY_SHORT_NAME")
                        or row.get("secName")
                        or ""
                    ).strip(),
                    "symbol": symbol,
                    "amount": amount,
                    "volume": int(_parse_float(row.get("count")) or 0),
                    "available_volume": int(_parse_float(row.get("availCount")) or 0),
                    "day_profit": _parse_float(row.get("dayProfit")),
                    "day_profit_ratio": day_profit_ratio,
                    "profit": _parse_float(row.get("profit")),
                    "profit_ratio": profit_ratio,
                    "profit_text": self._format_profit_text(profit_ratio),
                    "current_price": _scaled_decimal(
                        _coalesce(row.get("price"), row.get("currentPrice")),
                        _coalesce(row.get("priceDec"), row.get("priceDecimal")),
                    ),
                    "cost_price": _scaled_decimal(
                        _coalesce(row.get("costPrice"), row.get("cost_price")),
                        _coalesce(row.get("costPriceDec"), row.get("costPriceDecimal")),
                    ),
                    "position_ratio": position_ratio,
                }
            )

        normalized_positions.sort(key=lambda item: item["amount"], reverse=True)
        return {
            "open_date": open_date,
            "daily_profit_trade_date": daily_profit_trade_date,
            "operating_days": operating_days,
            "initial_capital": initial_capital,
            "total_assets": total_assets,
            "total_market_value": total_market_value,
            "cash_balance": cash_balance,
            "total_position_ratio": total_position_ratio,
            "holding_profit": holding_profit
            if holding_profit is not None
            else _parse_float(
                (positions_payload or {}).get("data", {}).get("totalProfit")
            )
            if isinstance((positions_payload or {}).get("data"), dict)
            else None,
            "daily_profit": daily_profit,
            "total_return_ratio": total_return_ratio,
            "nav": nav,
            "daily_return_ratio": daily_return_ratio,
            "positions": normalized_positions,
            "trade_summaries": [],
        }

    def _format_profit_text(self, profit_ratio: float | None) -> str:
        if profit_ratio is None:
            return "--"
        return f"{profit_ratio * 100:.2f}%"


aniu_service = AniuService()
