"""交易账户服务：CRUD、脱敏、Skills 状态与连通性测试（多账户方案 §7 / §9 / §13）。"""

from __future__ import annotations

import json
import logging
import secrets
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import AppSettings, TradingAccount
from app.schemas.accounts import (
    AccountLlmTestResult,
    AccountMxTestResult,
    AccountSkillListRead,
    AccountSkillStatus,
    TradingAccountCreate,
    TradingAccountRead,
    TradingAccountUpdate,
)
from app.services.account_context import parse_disabled_skill_ids, resolve_llm_config

logger = logging.getLogger(__name__)

_MASK_MARKER = "****"

# 更新时保持原值的敏感字段
_SENSITIVE_FIELDS = {"mx_api_key", "llm_api_key"}


class AccountService:
    # ── 查询 ──────────────────────────────────────────────────────────────

    def list_accounts(self, db: Session, *, include_archived: bool = False) -> list[TradingAccount]:
        stmt = select(TradingAccount).order_by(
            TradingAccount.sort_order.asc(),
            TradingAccount.id.asc(),
        )
        if not include_archived:
            stmt = stmt.where(TradingAccount.archived.is_(False))
        return list(db.scalars(stmt).all())

    def get_account(self, db: Session, account_id: int) -> TradingAccount | None:
        return db.get(TradingAccount, account_id)

    def require_account(self, db: Session, account_id: int) -> TradingAccount:
        account = self.get_account(db, account_id)
        if account is None:
            raise LookupError("交易账户不存在。")
        return account

    def count_active_accounts(self, db: Session) -> int:
        return len(self.list_accounts(db, include_archived=False))

    def get_default_account(self, db: Session) -> TradingAccount | None:
        return db.scalar(
            select(TradingAccount)
            .where(TradingAccount.slug == "default")
            .order_by(TradingAccount.id.asc())
            .limit(1)
        )

    def resolve_single_active_account(self, db: Session) -> TradingAccount | None:
        """旧接口兼容：只有一个未归档账户时自动映射。"""
        accounts = self.list_accounts(db, include_archived=False)
        if len(accounts) == 1:
            return accounts[0]
        return None

    # ── CRUD ─────────────────────────────────────────────────────────────

    def create_account(
        self, db: Session, payload: TradingAccountCreate
    ) -> TradingAccount:
        data = payload.model_dump()
        slug = str(data.pop("slug") or "").strip()
        if not slug:
            slug = f"acct-{secrets.token_hex(4)}"
        slug = slug.replace(" ", "-").lower()

        if db.scalar(
            select(TradingAccount).where(TradingAccount.slug == slug).limit(1)
        ) is not None:
            raise ValueError("账户 slug 已存在，请更换。")

        markets = data.pop("allowed_markets", None)
        from app.schemas.accounts import markets_to_json

        if markets is not None:
            data["allowed_markets_json"] = markets_to_json(markets)
        data["slug"] = slug
        account = TradingAccount(**data)
        db.add(account)
        db.flush()
        logger.info("trading account created: account_id=%s slug=%s", account.id, slug)
        return account

    def update_account(
        self, db: Session, account_id: int, payload: TradingAccountUpdate
    ) -> TradingAccount:
        account = self.require_account(db, account_id)
        data = payload.model_dump(exclude_none=True)

        markets = data.pop("allowed_markets", None)
        if markets is not None:
            from app.schemas.accounts import markets_to_json

            account.allowed_markets_json = markets_to_json(markets)

        changed_fields: list[str] = []
        for field, value in data.items():
            if field in _SENSITIVE_FIELDS:
                if isinstance(value, str) and _MASK_MARKER in value:
                    # 脱敏回写保护：含 **** 的字符串保持原值。
                    continue
                if isinstance(value, str) and not value.strip():
                    value = None
            old_value = getattr(account, field, None)
            if old_value != value:
                changed_fields.append(field)
            setattr(account, field, value)

        db.add(account)
        db.flush()
        logger.info(
            "trading account updated: account_id=%s changed_fields=%s",
            account_id,
            changed_fields,
        )
        return account

    def archive_account(self, db: Session, account_id: int) -> TradingAccount:
        account = self.require_account(db, account_id)
        if account.archived:
            return account
        if account.slug == "default":
            raise ValueError("默认账户不能归档。")
        account.archived = True
        db.add(account)
        db.flush()
        logger.info("trading account archived: account_id=%s", account_id)
        return account

    def restore_account(self, db: Session, account_id: int) -> TradingAccount:
        account = self.require_account(db, account_id)
        account.archived = False
        db.add(account)
        db.flush()
        logger.info("trading account restored: account_id=%s", account_id)
        return account

    # ── 账户 Skills（§9：全局硬禁用 ∪ 账户禁用） ─────────────────────────

    def _global_disabled_ids(self, db: Session) -> set[str]:
        settings = db.scalar(select(AppSettings).order_by(AppSettings.id).limit(1))
        if settings is None:
            return set()
        return set(parse_disabled_skill_ids(settings.disabled_skill_ids_json))

    def get_account_skills(self, db: Session, account_id: int) -> AccountSkillListRead:
        account = self.require_account(db, account_id)
        from app.skills.registry import skill_registry

        global_disabled = self._global_disabled_ids(db)
        account_disabled = set(
            parse_disabled_skill_ids(account.disabled_skill_ids_json)
        )
        account_disabled -= {
            skill_id
            for skill_id in account_disabled
            if skill_registry.is_system_runtime(skill_id)
        }

        statuses: list[AccountSkillStatus] = []
        for pkg in skill_registry.all_packages():
            statuses.append(
                AccountSkillStatus(
                    id=pkg.id,
                    name=pkg.name,
                    role="runtime" if pkg.always_enabled else "standard",
                    always_enabled=bool(pkg.always_enabled),
                    can_disable=bool(getattr(pkg, "can_disable", True)),
                    source=pkg.source,
                    global_disabled=pkg.id in global_disabled,
                    account_disabled=pkg.id in account_disabled,
                    effective_enabled=bool(
                        pkg.always_enabled
                        or (
                            pkg.id not in global_disabled
                            and pkg.id not in account_disabled
                        )
                    ),
                )
            )

        return AccountSkillListRead(
            global_available=statuses,
            global_hard_disabled=sorted(global_disabled),
            account_enabled=sorted(
                {
                    pkg.id
                    for pkg in statuses
                    if not pkg.always_enabled
                    and not pkg.global_disabled
                    and not pkg.account_disabled
                }
            ),
            effective_enabled=sorted(
                pkg.id for pkg in statuses if pkg.effective_enabled
            ),
            always_enabled=sorted(pkg.id for pkg in statuses if pkg.always_enabled),
        )

    def update_account_skills(
        self, db: Session, account_id: int, enabled_ids: list[str]
    ) -> AccountSkillListRead:
        account = self.require_account(db, account_id)
        from app.skills.registry import skill_registry

        global_disabled = self._global_disabled_ids(db)
        requested = set(str(skill_id or "").strip() for skill_id in enabled_ids)
        installed_ids = {pkg.id for pkg in skill_registry.all_packages()}
        requested &= installed_ids

        # 账户无法重新启用全局硬禁用技能。
        requested -= global_disabled
        # 系统运行时技能不可禁用，始终视为启用。
        for skill_id in list(requested):
            if skill_registry.is_system_runtime(skill_id):
                requested.discard(skill_id)

        account_disabled = {
            pkg.id
            for pkg in skill_registry.all_packages()
            if pkg.id not in requested
            and not skill_registry.is_system_runtime(pkg.id)
            and pkg.id not in global_disabled
        }
        account.disabled_skill_ids_json = json.dumps(
            sorted(account_disabled), ensure_ascii=False
        )
        db.add(account)
        db.flush()
        logger.info(
            "trading account skills updated: account_id=%s enabled=%s",
            account_id,
            sorted(requested),
        )
        return self.get_account_skills(db, account_id)

    # ── 连通性测试 ───────────────────────────────────────────────────────

    def test_mx(self, db: Session, account_id: int) -> AccountMxTestResult:
        account = self.require_account(db, account_id)
        api_key = str(account.mx_api_key or "").strip()
        if not api_key:
            return AccountMxTestResult(
                ok=False, message="账户未配置妙想 Key，请先保存。"
            )

        from app.core.config import get_settings
        from skills.mx_core.client import MXClient

        started = time.monotonic()
        try:
            with MXClient(
                api_key=api_key,
                base_url=get_settings().mx_api_url,
            ) as client:
                payload = client.get_balance()
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and data.get("totalAsset") is not None:
                return AccountMxTestResult(
                    ok=True,
                    message="妙想 API 连接正常，已读取账户资产。",
                    latency_ms=latency_ms,
                )
            return AccountMxTestResult(
                ok=True,
                message="妙想 API 返回正常。",
                latency_ms=latency_ms,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            logger.warning("test_mx failed: account_id=%s error=%s", account_id, exc)
            return AccountMxTestResult(
                ok=False,
                message=f"妙想 API 连接失败：{exc}",
                latency_ms=latency_ms,
            )

    def test_llm(self, db: Session, account_id: int) -> AccountLlmTestResult:
        account = self.require_account(db, account_id)
        settings = db.scalar(select(AppSettings).order_by(AppSettings.id).limit(1))
        if settings is None:
            return AccountLlmTestResult(ok=False, message="系统设置不存在。")
        try:
            config = resolve_llm_config(account, settings)
        except RuntimeError as exc:
            return AccountLlmTestResult(ok=False, message=str(exc), source="none")

        from app.services.llm_service import llm_service

        started = time.monotonic()
        try:
            content = llm_service.chat(
                model=config.model,
                base_url=str(config.base_url or ""),
                api_key=str(config.api_key or ""),
                system_prompt="你是一个连通性测试助手，请只回复：OK",
                messages=[{"role": "user", "content": "连通性测试"}],
                timeout_seconds=30,
                enable_reasoning_echo=config.enable_reasoning_content_echo,
                reasoning_effort=config.reasoning_effort,
            )
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            return AccountLlmTestResult(
                ok=True,
                message=f"大模型连接正常：{str(content or '').strip()[:120]}",
                source=config.source,
                model=config.model,
                latency_ms=latency_ms,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = round((time.monotonic() - started) * 1000, 1)
            logger.warning(
                "test_llm failed: account_id=%s source=%s error=%s",
                account_id,
                config.source,
                exc,
            )
            return AccountLlmTestResult(
                ok=False,
                message=f"大模型连接失败：{exc}",
                source=config.source,
                model=config.model,
                latency_ms=latency_ms,
            )

    # ── 序列化 ───────────────────────────────────────────────────────────

    def to_read(self, db: Session, account: TradingAccount) -> TradingAccountRead:
        settings = db.scalar(select(AppSettings).order_by(AppSettings.id).limit(1))
        has_account_llm = bool(
            str(account.llm_base_url or "").strip()
            and str(account.llm_api_key or "").strip()
            and str(account.llm_model or "").strip()
        )
        source: str = "none"
        if bool(account.account_llm_enabled) and has_account_llm:
            source = "account"
        elif settings is not None and (
            str(settings.llm_base_url or "").strip()
            and str(settings.llm_api_key or "").strip()
        ):
            source = "global"

        return TradingAccountRead(
            id=account.id,
            name=account.name,
            slug=account.slug,
            enabled=bool(account.enabled),
            archived=bool(account.archived),
            sort_order=int(account.sort_order or 0),
            mx_api_key=(
                _mask_middle(str(account.mx_api_key))
                if str(account.mx_api_key or "").strip()
                else None
            ),
            has_mx_api_key=bool(str(account.mx_api_key or "").strip()),
            account_llm_enabled=bool(account.account_llm_enabled),
            llm_provider_name=account.llm_provider_name,
            llm_base_url=account.llm_base_url,
            llm_api_key=(
                _mask_middle(str(account.llm_api_key))
                if str(account.llm_api_key or "").strip()
                else None
            ),
            llm_model=account.llm_model,
            llm_reasoning_effort=account.llm_reasoning_effort,
            llm_max_retries=account.llm_max_retries,
            llm_enable_reasoning_content_echo=bool(
                account.llm_enable_reasoning_content_echo
            ),
            has_account_llm_config=has_account_llm,
            resolved_llm_source=source,  # type: ignore[arg-type]
            system_prompt=account.system_prompt or "",
            analyst_prompt=account.analyst_prompt or "",
            market_query=account.market_query or "",
            news_query=account.news_query or "",
            screener_query=account.screener_query or "",
            max_actions=int(account.max_actions or 2),
            trade_enabled=bool(account.trade_enabled),
            allowed_markets=list(_parse_markets(account.allowed_markets_json)),
            tg_bot_token=account.tg_bot_token,
            tg_chat_id=account.tg_chat_id,
            tg_notify_trade_enabled=bool(account.tg_notify_trade_enabled),
            capital_seal_enabled=bool(account.capital_seal_enabled),
            capital_seal_amount=float(account.capital_seal_amount or 0),
            disabled_skill_ids=sorted(
                parse_disabled_skill_ids(account.disabled_skill_ids_json)
            ),
            automation_context_window_tokens=int(
                account.automation_context_window_tokens or 128000
            ),
            automation_recent_message_limit=int(
                account.automation_recent_message_limit or 24
            ),
            automation_enable_auto_compaction=bool(
                account.automation_enable_auto_compaction
            ),
            automation_idle_summary_hours=int(
                account.automation_idle_summary_hours or 12
            ),
            created_at=account.created_at,
            updated_at=account.updated_at,
        )


def _mask_middle(value: str) -> str:
    if not value:
        return value
    if len(value) <= 8:
        return "****" + value[-2:] if len(value) > 2 else "****"
    return value[:3] + "****" + value[-4:]


def _parse_markets(raw: str | None) -> list[str]:
    from skills.mx_core.markets import normalize_allowed_markets

    return normalize_allowed_markets(raw)


account_service = AccountService()
