from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.core.constants import DEFAULT_APP_DISPLAY_NAME, DEFAULT_SYSTEM_PROMPT


class Base(DeclarativeBase):
    pass


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    app_display_name: Mapped[str] = mapped_column(
        String(64), default=DEFAULT_APP_DISPLAY_NAME
    )
    provider_name: Mapped[str] = mapped_column(String(32), default="openai-compatible")
    mx_api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uzi_mx_api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    llm_base_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_api_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_model: Mapped[str] = mapped_column(String(128), default="gpt-4o-mini")
    llm_reasoning_effort: Mapped[str | None] = mapped_column(
        String(64), nullable=True, default=None
    )
    llm_max_retries: Mapped[int] = mapped_column(Integer, default=3)
    disabled_skill_ids_json: Mapped[str] = mapped_column(
        Text,
        default="[]",
    )
    allowed_markets_json: Mapped[str] = mapped_column(
        Text,
        default='["sh_main","sz_main"]',
    )
    system_prompt: Mapped[str] = mapped_column(
        Text,
        default=DEFAULT_SYSTEM_PROMPT,
    )
    analyst_prompt: Mapped[str] = mapped_column(
        Text,
        default=(
            "请结合市场数据、资讯、候选股票、持仓和资金情况做判断。"
            "当信号不明确时返回HOLD。"
        ),
    )
    market_query: Mapped[str] = mapped_column(
        String(255), default="上证指数今天走势和市场概况"
    )
    news_query: Mapped[str] = mapped_column(String(255), default="今天A股市场热点新闻")
    screener_query: Mapped[str] = mapped_column(
        String(255), default="A股今天值得关注的强势股"
    )
    max_actions: Mapped[int] = mapped_column(Integer, default=2)
    trade_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    automation_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    automation_context_window_tokens: Mapped[int | None] = mapped_column(
        Integer, nullable=True, default=128000
    )
    automation_recent_message_limit: Mapped[int] = mapped_column(
        Integer, default=24
    )
    automation_enable_auto_compaction: Mapped[bool] = mapped_column(
        Boolean, default=True
    )
    automation_idle_summary_hours: Mapped[int] = mapped_column(Integer, default=12)
    llm_enable_reasoning_content_echo: Mapped[bool] = mapped_column(Boolean, default=False)
    tg_bot_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tg_chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tg_notify_trade_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    capital_seal_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    capital_seal_amount: Mapped[float] = mapped_column(Float, default=0.0)
    automation_context_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default="default"
    )
    automation_context_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class TradingAccount(Base):
    """一个妙想 Key 对应一个独立交易账户。

    每个账户是独立的交易子系统：妙想 Key、提示词、市场范围、Skills、
    自动化会话、定时任务、运行与订单历史、总览缓存均按账户隔离。
    归档账户不物理删除。
    """

    __tablename__ = "trading_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    slug: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    mx_api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)

    account_llm_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_provider_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    llm_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    llm_api_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    llm_reasoning_effort: Mapped[str | None] = mapped_column(String(64), nullable=True)
    llm_max_retries: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_enable_reasoning_content_echo: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )

    system_prompt: Mapped[str] = mapped_column(Text, default=DEFAULT_SYSTEM_PROMPT)
    analyst_prompt: Mapped[str] = mapped_column(
        Text,
        default=(
            "请结合市场数据、资讯、候选股票、持仓和资金情况做判断。"
            "当信号不明确时返回HOLD。"
        ),
    )
    market_query: Mapped[str] = mapped_column(
        String(255), default="上证指数今天走势和市场概况"
    )
    news_query: Mapped[str] = mapped_column(String(255), default="今天A股市场热点新闻")
    screener_query: Mapped[str] = mapped_column(
        String(255), default="A股今天值得关注的强势股"
    )
    max_actions: Mapped[int] = mapped_column(Integer, default=2)
    trade_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    allowed_markets_json: Mapped[str] = mapped_column(
        Text, default='["sh_main","sz_main"]'
    )

    disabled_skill_ids_json: Mapped[str] = mapped_column(Text, default="[]")

    automation_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    automation_context_window_tokens: Mapped[int] = mapped_column(
        Integer, default=128000
    )
    automation_recent_message_limit: Mapped[int] = mapped_column(
        Integer, default=24
    )
    automation_enable_auto_compaction: Mapped[bool] = mapped_column(
        Boolean, default=True
    )
    automation_idle_summary_hours: Mapped[int] = mapped_column(Integer, default=12)
    automation_context_source: Mapped[str | None] = mapped_column(
        String(32), nullable=True, default="default"
    )
    automation_context_detected_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    tg_bot_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tg_chat_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    tg_notify_trade_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    capital_seal_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    capital_seal_amount: Mapped[float] = mapped_column(Float, default=0.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class StrategySchedule(Base):
    __tablename__ = "strategy_schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    trading_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("trading_accounts.id"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), default="默认调度任务")
    run_type: Mapped[str] = mapped_column(String(32), default="analysis")
    interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    cron_expression: Mapped[str | None] = mapped_column(String(64), nullable=True)
    task_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    retry_after_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class StrategyRun(Base):
    __tablename__ = "strategy_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    trading_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("trading_accounts.id"),
        nullable=True,
        index=True,
    )
    trading_account_name_snapshot: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    llm_config_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    llm_model_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    trigger_source: Mapped[str] = mapped_column(String(32), default="manual")
    run_type: Mapped[str] = mapped_column(String(32), default="analysis")
    schedule_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    schedule_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_session_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    prompt_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_summary_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    context_tokens_estimate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    analysis_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_request_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    llm_response_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    skill_payloads: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    decision_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    executed_actions: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    # 预计算汇总指标（run 完成时写入；老行由 _backfill_strategy_run_metrics 回填）。
    # 列表查询直接读这些列，避免为每行反序列化超大的 llm_*_payload / skill_payloads。
    api_call_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    executed_trade_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    trade_orders: Mapped[list["TradeOrder"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class TradeOrder(Base):
    __tablename__ = "trade_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("strategy_runs.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    action: Mapped[str] = mapped_column(String(16))
    quantity: Mapped[int] = mapped_column(Integer)
    price_type: Mapped[str] = mapped_column(String(16), default="MARKET")
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="submitted")
    response_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    run: Mapped[StrategyRun] = relationship(back_populates="trade_orders")


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    trading_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("trading_accounts.id"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(120), default="新对话")
    kind: Mapped[str] = mapped_column(String(32), default="user", index=True)
    slug: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    archived_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_compacted_message_id: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    last_compacted_run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary_revision: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, index=True
    )

    messages: Mapped[list["ChatMessageRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessageRecord.id",
    )


class ChatMessageRecord(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    message_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    meta_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    attachments: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class ChatAttachment(Base):
    __tablename__ = "chat_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    trading_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("trading_accounts.id"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UziReportJob(Base):
    """UZI 深度报告任务（文档 §9）。

    状态机见文档 §6：queued → stage1_running → llm_review → stage2_running
    → completed；任一非终态都可进入 failed / cancelled。禁止状态倒退，
    重试必须创建新任务。
    """

    __tablename__ = "uzi_report_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker_input: Mapped[str] = mapped_column(String(64))
    ticker_normalized: Mapped[str | None] = mapped_column(String(32), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    progress_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    worker_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uzi_commit: Mapped[str] = mapped_column(String(40), default="")
    llm_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    llm_reasoning_effort: Mapped[str | None] = mapped_column(String(32), nullable=True)
    data_as_of: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    summary_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    artifact_manifest_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    report_rel_dir: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
