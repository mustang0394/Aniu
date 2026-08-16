"""UZI LLM 工具双重 allowlist 测试（文档 §13.2 / §20.3）。

- ``uzi_analysis`` 工具列表只包含硬编码 allowlist。
- 工具执行器拒绝伪造的 mx_moni_trade / exec 等调用。
"""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.services.uzi_llm_orchestrator import (
    UZI_LLM_ALLOWED_TOOLS,
    UZI_LLM_FORBIDDEN_TOOLS,
    UziLlmOrchestrator,
)
from app.skills import skill_registry


def test_uzi_analysis_profile_readonly() -> None:
    """mx_core TOOL_PROFILES 的 uzi_analysis 只含只读查询工具。"""
    from skills.mx_core.tool_specs import TOOL_PROFILES

    profile = TOOL_PROFILES["uzi_analysis"]
    assert profile == {"mx_query_market", "mx_search_news"}
    assert profile.isdisjoint(UZI_LLM_FORBIDDEN_TOOLS)


def test_build_tools_only_allowlist() -> None:
    skill_registry.reload()
    orchestrator = UziLlmOrchestrator()
    tools = orchestrator.build_tools()
    names = {
        spec.get("function", {}).get("name")
        for spec in tools
        if spec.get("function", {}).get("name")
    }
    assert names == set(UZI_LLM_ALLOWED_TOOLS)


def test_forbidden_tools_never_in_allowlist() -> None:
    assert UZI_LLM_ALLOWED_TOOLS.isdisjoint(UZI_LLM_FORBIDDEN_TOOLS)


def test_execute_rejects_forged_mutation_tool() -> None:
    orchestrator = UziLlmOrchestrator()
    for forged_name in ("mx_moni_trade", "mx_moni_cancel", "exec", "write_file",
                        "http_post", "edit_file", "mx_manage_self_select"):
        result = orchestrator.execute_tool(
            tool_name=forged_name,
            arguments={"action": "BUY", "symbol": "600519"},
            context={"run_type": "uzi_analysis"},
        )
        assert result["ok"] is False, forged_name
        assert "允许集合" in result["error"], forged_name


def test_execute_allows_readonly_tools_structure() -> None:
    """allowlist 内工具放行给 skill_registry（不校验网络，只确认不分流）。"""
    orchestrator = UziLlmOrchestrator()

    def _fake_registry_execute(*, tool_name, arguments, context):
        return {"ok": True, "tool_name": tool_name, "result": {"fake": True}}

    import app.services.uzi_llm_orchestrator as orchestrator_module

    original = orchestrator_module.skill_registry.execute_tool
    orchestrator_module.skill_registry.execute_tool = _fake_registry_execute
    try:
        result = orchestrator.execute_tool(
            tool_name="mx_query_market",
            arguments={"query": "贵州茅台"},
            context={"run_type": "uzi_analysis"},
        )
        assert result["ok"] is True
        assert result["result"] == {"fake": True}
    finally:
        orchestrator_module.skill_registry.execute_tool = original