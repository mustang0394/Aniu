"""Provider assembly helpers for skill execution contexts."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db.database import session_scope


def build_skill_context(
    *,
    run_type: str | None = None,
    app_settings: Any = None,
    client: Any = None,
    base_context: dict[str, Any] | None = None,
    trading_account_id: int | None = None,
    trading_account_name: str | None = None,
    account_context: Any = None,
) -> dict[str, Any]:
    """组装技能执行上下文。

    账户化运行（阶段 5 §9.3）：妙想工具必须从 context 的 client /
    mx_client_config 调用；账户 ID、名称、禁用 Skills 集合随上下文注入，
    禁止 Handler 自己查询全局 AppSettings 推导账户。
    """
    from app.services.aniu_service import aniu_service

    context = dict(base_context or {})
    normalized_run_type = str(run_type or context.get("run_type") or "analysis").strip() or "analysis"
    context["run_type"] = normalized_run_type

    if app_settings is not None:
        context["app_settings"] = app_settings
    if client is not None:
        context["client"] = client

    if trading_account_id is not None:
        context["trading_account_id"] = trading_account_id
    if trading_account_name is not None:
        context["trading_account_name"] = trading_account_name
    if account_context is not None:
        context["account_context"] = account_context

    account_disabled = getattr(app_settings, "disabled_skill_ids", None)
    if account_disabled is not None and "disabled_skill_ids" not in context:
        context["disabled_skill_ids"] = account_disabled

    context.setdefault(
        "chat_context_ports",
        {
            "get_account_overview": aniu_service.get_account_overview,
            "list_runs_page": aniu_service.list_runs_page,
            "get_run": aniu_service.get_run,
            "session_scope_factory": session_scope,
        },
    )

    app_settings_value = context.get("app_settings")
    mx_api_key = getattr(app_settings_value, "mx_api_key", None)
    mx_api_url = getattr(app_settings_value, "mx_api_url", None)
    context.setdefault(
        "mx_client_config",
        {
            "api_key": mx_api_key,
            "base_url": mx_api_url,
        },
    )

    skill_runtime_paths = context.get("skill_runtime_paths")
    if not isinstance(skill_runtime_paths, dict):
        skill_runtime_paths = {}
    skill_runtime_paths.setdefault(
        "builtin_skills_root",
        str(Path(__file__).resolve().parents[2] / "skills"),
    )
    context["skill_runtime_paths"] = skill_runtime_paths

    return context
