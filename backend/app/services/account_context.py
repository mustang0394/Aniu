"""账户运行上下文：不可变的账户级配置快照（多账户方案 §3 / §7.2 / §7.3）。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AppSettings, ChatSession, StrategySchedule, TradingAccount
from skills.mx_core.markets import normalize_allowed_markets

logger = logging.getLogger(__name__)

AUTOMATION_SESSION_SLUG_PREFIX = "automation-"
AUTOMATION_SESSION_TITLE = "自动化交易会话"
AUTOMATION_DEFAULT_CONTEXT_WINDOW_TOKENS = 128000
AUTOMATION_DEFAULT_RECENT_MESSAGE_LIMIT = 24
AUTOMATION_DEFAULT_IDLE_SUMMARY_HOURS = 12

_RESERVED_SLUGS = {"default"}


@dataclass(frozen=True)
class ResolvedLLMConfig:
    """账户或全局解析后的 LLM 配置。账户配置必须整套有效才使用。"""

    provider_name: str
    base_url: str | None
    api_key: str | None
    model: str
    reasoning_effort: str | None
    max_retries: int
    enable_reasoning_content_echo: bool
    source: Literal["account", "global"]


def resolve_llm_config(
    account: TradingAccount | Any,
    global_settings: AppSettings | Any,
) -> ResolvedLLMConfig:
    """解析账户 LLM；不完整或未启用时整体回退全局。"""
    if bool(getattr(account, "account_llm_enabled", False)):
        account_base_url = str(getattr(account, "llm_base_url", "") or "").strip()
        account_api_key = str(getattr(account, "llm_api_key", "") or "").strip()
        account_model = str(getattr(account, "llm_model", "") or "").strip()
        if account_base_url and account_api_key and account_model:
            return ResolvedLLMConfig(
                provider_name=str(
                    getattr(account, "llm_provider_name", None) or ""
                ).strip()
                or str(getattr(global_settings, "provider_name", "openai-compatible")),
                base_url=account_base_url,
                api_key=account_api_key,
                model=account_model,
                reasoning_effort=(
                    str(getattr(account, "llm_reasoning_effort", None) or "").strip()
                    or None
                ),
                max_retries=int(
                    getattr(account, "llm_max_retries", None)
                    or getattr(global_settings, "llm_max_retries", 3)
                    or 3
                ),
                enable_reasoning_content_echo=bool(
                    getattr(account, "llm_enable_reasoning_content_echo", None)
                    if getattr(account, "llm_enable_reasoning_content_echo", None)
                    is not None
                    else getattr(global_settings, "llm_enable_reasoning_content_echo", False)
                ),
                source="account",
            )

    global_base_url = str(getattr(global_settings, "llm_base_url", "") or "").strip()
    global_api_key = str(getattr(global_settings, "llm_api_key", "") or "").strip()
    if global_base_url and global_api_key:
        return ResolvedLLMConfig(
            provider_name=str(
                getattr(global_settings, "provider_name", "openai-compatible")
            ),
            base_url=global_base_url,
            api_key=global_api_key,
            model=str(getattr(global_settings, "llm_model", "") or "").strip()
            or "gpt-4o-mini",
            reasoning_effort=(
                str(getattr(global_settings, "llm_reasoning_effort", None) or "").strip()
                or None
            ),
            max_retries=int(getattr(global_settings, "llm_max_retries", 3) or 3),
            enable_reasoning_content_echo=bool(
                getattr(global_settings, "llm_enable_reasoning_content_echo", False)
            ),
            source="global",
        )

    raise RuntimeError("账户和全局大模型配置均不可用。")


def parse_disabled_skill_ids(raw: str | None) -> frozenset[str]:
    text = str(raw or "").strip()
    if not text:
        return frozenset()
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = [part.strip() for part in text.split(",") if part.strip()]
    if not isinstance(parsed, list):
        return frozenset()
    return frozenset(
        str(item).strip() for item in parsed if str(item or "").strip()
    )


@dataclass(frozen=True)
class AccountRunContext:
    account_id: int
    account_name: str
    run_type: str
    schedule_id: int | None
    schedule_name: str | None
    task_prompt: str

    mx_api_key: str | None
    mx_api_base_url: str
    llm: ResolvedLLMConfig

    system_prompt: str
    analyst_prompt: str
    market_query: str
    news_query: str
    screener_query: str
    allowed_markets: tuple[str, ...]
    max_actions: int
    trade_enabled: bool

    disabled_skill_ids: frozenset[str] = field(default_factory=frozenset)

    automation_session_id: int | None = None
    automation_context_window_tokens: int = AUTOMATION_DEFAULT_CONTEXT_WINDOW_TOKENS
    automation_recent_message_limit: int = AUTOMATION_DEFAULT_RECENT_MESSAGE_LIMIT
    automation_enable_auto_compaction: bool = True
    automation_idle_summary_hours: int = AUTOMATION_DEFAULT_IDLE_SUMMARY_HOURS

    tg_bot_token: str | None = None
    tg_chat_id: str | None = None
    tg_notify_trade_enabled: bool = False

    capital_seal_enabled: bool = False
    capital_seal_amount: float = 0.0

    def to_settings_snapshot(self) -> dict[str, Any]:
        """转为运行链可用的 dict 快照（兼容 SimpleNamespace 消费方）。"""
        return {
            "account_id": self.account_id,
            "account_name": self.account_name,
            "run_type": self.run_type,
            "schedule_id": self.schedule_id,
            "schedule_name": self.schedule_name,
            "task_prompt": self.task_prompt,
            "mx_api_key": self.mx_api_key,
            "mx_api_base_url": self.mx_api_base_url,
            "llm_base_url": self.llm.base_url,
            "llm_api_key": self.llm.api_key,
            "llm_model": self.llm.model,
            "llm_reasoning_effort": self.llm.reasoning_effort,
            "llm_max_retries": self.llm.max_retries,
            "llm_enable_reasoning_content_echo": self.llm.enable_reasoning_content_echo,
            "provider_name": self.llm.provider_name,
            "system_prompt": self.system_prompt,
            "analyst_prompt": self.analyst_prompt,
            "market_query": self.market_query,
            "news_query": self.news_query,
            "screener_query": self.screener_query,
            "allowed_markets": list(self.allowed_markets),
            "allowed_markets_json": json.dumps(
                list(self.allowed_markets), ensure_ascii=False
            ),
            "max_actions": self.max_actions,
            "trade_enabled": self.trade_enabled,
            "disabled_skill_ids": self.disabled_skill_ids,
            "automation_session_id": self.automation_session_id,
            "automation_context_window_tokens": self.automation_context_window_tokens,
            "automation_recent_message_limit": self.automation_recent_message_limit,
            "automation_enable_auto_compaction": self.automation_enable_auto_compaction,
            "automation_idle_summary_hours": self.automation_idle_summary_hours,
            "tg_bot_token": self.tg_bot_token,
            "tg_chat_id": self.tg_chat_id,
            "tg_notify_trade_enabled": self.tg_notify_trade_enabled,
            "capital_seal_enabled": self.capital_seal_enabled,
            "capital_seal_amount": self.capital_seal_amount,
        }


def get_or_create_automation_session(
    db: Session,
    *,
    account_id: int,
    previous_session_id: int | None = None,
) -> ChatSession:
    """按账户查找或创建自动化会话（slug=automation-{account_id}）。

    兼容旧库：若账户记录了 automation_session_id 且会话属于该账户，直接复用。
    """
    slug = f"{AUTOMATION_SESSION_SLUG_PREFIX}{account_id}"
    if previous_session_id:
        existing = db.get(ChatSession, previous_session_id)
        if (
            existing is not None
            and str(existing.kind or "") == "automation"
            and int(existing.trading_account_id or 0) == account_id
        ):
            return existing

    session = db.scalar(
        select(ChatSession).where(
            ChatSession.kind == "automation",
            ChatSession.trading_account_id == account_id,
            ChatSession.slug == slug,
        )
    )
    if session is None:
        session = ChatSession(
            title=AUTOMATION_SESSION_TITLE,
            kind="automation",
            slug=slug,
            trading_account_id=account_id,
        )
        db.add(session)
        db.flush()
    return session


def build_account_run_context(
    db: Session,
    *,
    account_id: int,
    schedule_id: int | None,
    manual_run_type: str | None,
    global_settings: AppSettings | None = None,
) -> AccountRunContext:
    """构造账户运行上下文（§7.3）。

    校验账户启用/未归档、任务归属；解析 run_type、task_prompt、LLM、
    账户 Skills 与自动化会话。所有字段来自账户，运行链不得再读全局交易配置。
    """
    settings = global_settings or db.scalar(
        select(AppSettings).order_by(AppSettings.id).limit(1)
    )
    if settings is None:
        from app.core.constants import DEFAULT_APP_DISPLAY_NAME

        settings = AppSettings(
            app_display_name=DEFAULT_APP_DISPLAY_NAME,
            provider_name="openai-compatible",
            system_prompt="你是专业的 A 股交易分析师。",
        )
        db.add(settings)
        db.flush()

    account = db.get(TradingAccount, account_id)
    if account is None:
        raise RuntimeError("交易账户不存在。")
    if not account.enabled:
        raise RuntimeError("交易账户已停用，无法运行任务。")
    if account.archived:
        raise RuntimeError("交易账户已归档，无法运行任务。")

    schedule: StrategySchedule | None = None
    if schedule_id is not None:
        schedule = db.get(StrategySchedule, schedule_id)
        if schedule is None:
            raise RuntimeError("指定的定时任务不存在。")
        if schedule.trading_account_id is None:
            # 旧库/未迁移记录自动归属到当前账户。
            schedule.trading_account_id = account_id
            db.add(schedule)
        elif int(schedule.trading_account_id) != account_id:
            raise RuntimeError("指定的定时任务不属于该交易账户。")

    normalized_manual = str(manual_run_type or "").strip().lower()
    if normalized_manual == "trade":
        run_type = "trade"
    elif normalized_manual == "analysis":
        run_type = "analysis"
    elif schedule is not None and str(schedule.run_type or "").strip() in {
        "analysis",
        "trade",
    }:
        run_type = str(schedule.run_type).strip()
    else:
        run_type = "analysis"

    if normalized_manual == "trade":
        task_prompt = (
            "请根据当前市场、持仓和资金情况生成交易决策并执行。"
            "你必须调用妙想工具获取最新数据，当判断需要买入或卖出时，"
            "必须通过调用 mx_moni_trade 工具实际下单"
            "（不要在文本中仅描述交易意图，不调用函数 = 交易不会发生）。"
            "分析完毕后用自然语言总结本次交易判断、依据和操作结果。"
        )
    elif schedule is not None and str(schedule.task_prompt or "").strip():
        task_prompt = str(schedule.task_prompt).strip()
    elif str(getattr(settings, "task_prompt", "") or "").strip():
        task_prompt = str(settings.task_prompt).strip()
    else:
        task_prompt = (
            "请先调用妙想工具获取最新行情、资讯、持仓与资金数据，基于数据给出分析结论，"
            "并在需要时执行模拟交易。最后用自然语言总结本次判断、依据和操作结果。"
        )

    llm = resolve_llm_config(account, settings)

    from app.core.config import get_settings as get_env_settings

    mx_base_url = str(get_env_settings().mx_api_url or "").strip() or (
        "https://mkapi2.dfcfs.com/finskillshub"
    )

    # 账户 Key 优先；旧库升级场景账户 Key 为空时回退全局 Key。
    mx_api_key = str(account.mx_api_key or "").strip() or str(
        getattr(settings, "mx_api_key", None) or ""
    ).strip() or None

    automation_session = get_or_create_automation_session(
        db,
        account_id=account_id,
        previous_session_id=int(getattr(account, "automation_session_id", 0) or 0),
    )
    account.automation_session_id = automation_session.id

    allowed_markets = tuple(
        normalize_allowed_markets(
            getattr(account, "allowed_markets_json", None) or None
        )
    )
    disabled_skill_ids = parse_disabled_skill_ids(
        getattr(account, "disabled_skill_ids_json", None)
    )

    return AccountRunContext(
        account_id=account_id,
        account_name=str(account.name or "").strip() or f"账户{account_id}",
        run_type=run_type,
        schedule_id=schedule.id if schedule else None,
        schedule_name=schedule.name if schedule else None,
        task_prompt=task_prompt,
        mx_api_key=mx_api_key,
        mx_api_base_url=mx_base_url,
        llm=llm,
        system_prompt=str(account.system_prompt or ""),
        analyst_prompt=str(account.analyst_prompt or ""),
        market_query=str(account.market_query or ""),
        news_query=str(account.news_query or ""),
        screener_query=str(account.screener_query or ""),
        allowed_markets=allowed_markets,
        max_actions=int(account.max_actions or 2),
        trade_enabled=bool(account.trade_enabled),
        disabled_skill_ids=disabled_skill_ids,
        automation_session_id=automation_session.id,
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
        tg_bot_token=str(account.tg_bot_token or "").strip() or None,
        tg_chat_id=str(account.tg_chat_id or "").strip() or None,
        tg_notify_trade_enabled=bool(account.tg_notify_trade_enabled),
        capital_seal_enabled=bool(account.capital_seal_enabled),
        capital_seal_amount=float(account.capital_seal_amount or 0),
    )
