from __future__ import annotations

from typing import Any

from app.skills.base import BaseSkill
from app.skills.context import get_mx_client_config
from skills.mx_core.client import MXClient
from skills.mx_core.execution import mx_execution_service
from skills.mx_core.tool_specs import TOOL_PROFILES


def _load_specs() -> list[dict[str, Any]]:
    return mx_execution_service.build_tools(run_type=None)


class Skill(BaseSkill):
    id = "mx_core"
    name = "妙想核心"
    description = "东方财富妙想 OpenAPI 与 A 股模拟交易工具集"
    run_types = ["analysis", "trade", "chat", "uzi_analysis"]

    def __init__(self) -> None:
        self.tools = _load_specs()
        self.tool_run_type_filter = {}
        for run_type, tool_names in TOOL_PROFILES.items():
            for tool_name in tool_names:
                self.tool_run_type_filter.setdefault(tool_name, set()).add(run_type)

    _TRADE_TOOL_NAMES = {"mx_moni_trade", "mx_moni_cancel"}

    def _check_max_actions(
        self, *, tool_name: str, context: dict[str, Any]
    ) -> None:
        if tool_name not in self._TRADE_TOOL_NAMES:
            return
        app_settings = context.get("app_settings")
        max_actions = getattr(app_settings, "max_actions", None)
        try:
            limit = int(max_actions or 0)
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0:
            return
        counter = context.setdefault("_aniu_trade_action_count", {"count": 0})
        counter["count"] += 1
        if counter["count"] > limit:
            raise RuntimeError(
                f"本账户每轮最多执行 {limit} 次交易动作（买入/卖出/撤单），"
                f"已达上限，请停止交易操作并输出结论。"
            )

    def handle(self, *, tool_name, arguments, context):
        app_settings = context.get("app_settings")
        client = context.get("client")
        self._check_max_actions(tool_name=tool_name, context=context or {})

        def _execute() -> dict[str, Any]:
            return mx_execution_service.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                client=client,
                app_settings=app_settings,
            )

        if client is not None:
            return _execute()

        config = get_mx_client_config(context)
        with MXClient(api_key=config.api_key, base_url=config.base_url) as runtime_client:
            return mx_execution_service.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                client=runtime_client,
                app_settings=app_settings,
            )


# Compatibility export for tests and any lingering imports that still patch the
# old symbol from this module.
mx_skill_service = mx_execution_service
