"""账户层 Schema（多妙想 Key 多账户实现方案 §7.1 / §13）。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.aniu import MarketKey, _mask_key
from skills.mx_core.markets import (
    DEFAULT_ALLOWED_MARKETS,
    dumps_allowed_markets,
    normalize_allowed_markets,
)


def _normalize_float(value: Any) -> float:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return 0.0
    return amount if amount > 0 else 0.0


def _normalize_reasoning_effort(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_max_retries(value: Any) -> int | None:
    if value is None:
        return None
    try:
        retries = int(value)
    except (TypeError, ValueError):
        return None
    return max(0, min(10, retries))


class TradingAccountBase(BaseModel):
    name: str = Field(default="新账户", max_length=64)
    enabled: bool = True
    account_llm_enabled: bool = False
    llm_provider_name: str | None = Field(default=None, max_length=32)
    llm_base_url: str | None = Field(default=None, max_length=512)
    llm_api_key: str | None = Field(default=None, max_length=512)
    llm_model: str | None = Field(default=None, max_length=128)
    llm_reasoning_effort: str | None = Field(default=None, max_length=64)
    llm_max_retries: int | None = Field(default=None, ge=0, le=10)
    llm_enable_reasoning_content_echo: bool | None = None
    system_prompt: str = Field(default="你是专业的 A 股交易分析师。", max_length=20000)
    analyst_prompt: str = Field(
        default=(
            "请结合市场数据、资讯、候选股票、持仓和资金情况做判断。"
            "当信号不明确时返回HOLD。"
        ),
        max_length=20000,
    )
    market_query: str = Field(default="上证指数今天走势和市场概况", max_length=255)
    news_query: str = Field(default="今天A股市场热点新闻", max_length=255)
    screener_query: str = Field(default="A股今天值得关注的强势股", max_length=255)
    max_actions: int = Field(default=2, ge=1, le=20)
    trade_enabled: bool = True
    allowed_markets: list[MarketKey] = Field(
        default_factory=lambda: list(DEFAULT_ALLOWED_MARKETS),
        min_length=1,
    )
    tg_bot_token: str | None = Field(default=None, max_length=512)
    tg_chat_id: str | None = Field(default=None, max_length=512)
    tg_notify_trade_enabled: bool = False
    capital_seal_enabled: bool = False
    capital_seal_amount: float = Field(default=0.0, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "allowed_markets" in payload:
            payload["allowed_markets"] = normalize_allowed_markets(
                payload["allowed_markets"]
            )
        if "capital_seal_amount" in payload:
            payload["capital_seal_amount"] = _normalize_float(
                payload["capital_seal_amount"]
            )
        if "llm_reasoning_effort" in payload:
            payload["llm_reasoning_effort"] = _normalize_reasoning_effort(
                payload["llm_reasoning_effort"]
            )
        if "llm_max_retries" in payload:
            payload["llm_max_retries"] = _normalize_max_retries(
                payload["llm_max_retries"]
            )
        return payload


class TradingAccountCreate(TradingAccountBase):
    slug: str = Field(default="", max_length=96)
    mx_api_key: str | None = Field(default=None, max_length=512)


class TradingAccountUpdate(BaseModel):
    """可选字段更新；未提供的字段保持不变。

    - 字符串含 **** 时保持原值（脱敏回写保护）。
    - 空字符串明确清除（密钥类字段）。
    - slug 不可修改。
    """

    name: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    mx_api_key: str | None = Field(default=None, max_length=512)
    account_llm_enabled: bool | None = None
    llm_provider_name: str | None = Field(default=None, max_length=32)
    llm_base_url: str | None = Field(default=None, max_length=512)
    llm_api_key: str | None = Field(default=None, max_length=512)
    llm_model: str | None = Field(default=None, max_length=128)
    llm_reasoning_effort: str | None = Field(default=None, max_length=64)
    llm_max_retries: int | None = Field(default=None, ge=0, le=10)
    llm_enable_reasoning_content_echo: bool | None = None
    system_prompt: str | None = Field(default=None, max_length=20000)
    analyst_prompt: str | None = Field(default=None, max_length=20000)
    market_query: str | None = Field(default=None, max_length=255)
    news_query: str | None = Field(default=None, max_length=255)
    screener_query: str | None = Field(default=None, max_length=255)
    max_actions: int | None = Field(default=None, ge=1, le=20)
    trade_enabled: bool | None = None
    allowed_markets: list[MarketKey] | None = None
    tg_bot_token: str | None = Field(default=None, max_length=512)
    tg_chat_id: str | None = Field(default=None, max_length=512)
    tg_notify_trade_enabled: bool | None = None
    capital_seal_enabled: bool | None = None
    capital_seal_amount: float | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        payload = dict(value)
        if "allowed_markets" in payload and payload["allowed_markets"] is not None:
            payload["allowed_markets"] = normalize_allowed_markets(
                payload["allowed_markets"]
            )
        if "capital_seal_amount" in payload and payload["capital_seal_amount"] is not None:
            payload["capital_seal_amount"] = _normalize_float(
                payload["capital_seal_amount"]
            )
        if "llm_reasoning_effort" in payload:
            payload["llm_reasoning_effort"] = _normalize_reasoning_effort(
                payload["llm_reasoning_effort"]
            )
        if "llm_max_retries" in payload:
            payload["llm_max_retries"] = _normalize_max_retries(
                payload["llm_max_retries"]
            )
        return payload


class TradingAccountRead(TradingAccountBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    archived: bool
    sort_order: int
    mx_api_key: str | None = None
    has_mx_api_key: bool = False
    has_account_llm_config: bool = False
    resolved_llm_source: Literal["account", "global", "none"] = "none"
    disabled_skill_ids: list[str] = Field(default_factory=list)
    automation_context_window_tokens: int = 128000
    automation_recent_message_limit: int = 24
    automation_enable_auto_compaction: bool = True
    automation_idle_summary_hours: int = 12
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def mask_sensitive(self) -> "TradingAccountRead":
        self.mx_api_key = _mask_key(self.mx_api_key) if self.has_mx_api_key else None
        if self.llm_api_key:
            self.llm_api_key = _mask_key(self.llm_api_key)
        return self


class AccountSkillStatus(BaseModel):
    id: str
    name: str
    role: Literal["runtime", "standard"]
    always_enabled: bool
    can_disable: bool
    source: Literal["builtin", "workspace"]
    global_disabled: bool = False
    account_disabled: bool = False
    effective_enabled: bool = True


class AccountSkillListRead(BaseModel):
    global_available: list[AccountSkillStatus] = Field(default_factory=list)
    global_hard_disabled: list[str] = Field(default_factory=list)
    account_enabled: list[str] = Field(default_factory=list)
    effective_enabled: list[str] = Field(default_factory=list)
    always_enabled: list[str] = Field(default_factory=list)


class AccountMxTestResult(BaseModel):
    ok: bool
    message: str
    latency_ms: float | None = None


class AccountLlmTestResult(BaseModel):
    ok: bool
    message: str
    source: Literal["account", "global", "none"] = "none"
    model: str | None = None
    latency_ms: float | None = None


def markets_to_json(markets: list[MarketKey] | None) -> str | None:
    if markets is None:
        return None
    return dumps_allowed_markets(markets)
