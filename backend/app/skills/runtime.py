"""Skill runtime that builds tool lists, executes tools, and renders prompts."""
from __future__ import annotations

from collections.abc import Collection
from typing import Any

from app.skills.catalog import SkillCatalog
from app.skills.policy import SkillPolicy


class SkillRuntime:
    def __init__(self, *, catalog: SkillCatalog, policy: SkillPolicy) -> None:
        self._catalog = catalog
        self._policy = policy

    def iter_active_packages(
        self, disabled_skill_ids: Collection[str] | None = None
    ) -> list[Any]:
        return self._catalog.enabled_packages(
            extra_disabled=set(disabled_skill_ids or ())
        )

    def build_tools(
        self,
        *,
        run_type: str | None = None,
        disabled_skill_ids: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        rt = str(run_type or "analysis").strip() or "analysis"
        collected: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        packages = sorted(
            self.iter_active_packages(disabled_skill_ids),
            key=self._policy.tool_sort_key,
        )
        for pkg in packages:
            if pkg.skill is None or not pkg.supports_run_type(rt):
                continue
            for spec in pkg.skill.tools_for(rt):
                name = spec.get("function", {}).get("name")
                if not name or name in seen_names:
                    continue
                seen_names.add(name)
                collected.append(spec)
        return collected

    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
        disabled_skill_ids: Collection[str] | None = None,
    ) -> dict[str, Any]:
        for pkg in self.iter_active_packages(disabled_skill_ids):
            if pkg.skill is None:
                continue
            if tool_name in pkg.skill.tool_names():
                try:
                    return pkg.skill.handle(
                        tool_name=tool_name,
                        arguments=arguments,
                        context=context,
                    )
                except Exception as exc:  # noqa: BLE001
                    return {
                        "ok": False,
                        "tool_name": tool_name,
                        "error": str(exc),
                    }
        return {
            "ok": False,
            "tool_name": tool_name,
            "error": f"未知工具调用: {tool_name}",
        }

    def build_prompt_supplement(
        self,
        *,
        run_type: str | None = None,
        disabled_skill_ids: Collection[str] | None = None,
    ) -> str:
        rt = str(run_type or "analysis").strip() or "analysis"
        parts: list[str] = []
        active_packages = self.iter_active_packages(disabled_skill_ids)

        runtime_tools = self._policy.runtime_tool_names(
            active_packages,
            run_type=rt,
        )
        if runtime_tools:
            parts.append(
                "\n".join(
                    [
                        "## 技能运行时",
                        "以下通用运行时工具始终可用，可作为所有技能的执行底座："
                        + ", ".join(f"`{name}`" for name in runtime_tools),
                        "`read_file` 只适用于纯文本文件；不要对 PDF、图片、docx/xlsx/pptx 等二进制附件调用 `read_file`。",
                        "处理大文件或多参考文件时，优先使用 `glob` / `grep` 缩小范围，再读取具体文件。",
                        "所有写入与命令执行仅允许在 skill workspace 内进行；内置技能文档可以读取，但不能修改。",
                    ]
                )
            )

        prompt_packages = self._policy.prompt_packages(
            active_packages,
            run_type=rt,
        )
        always_packages = [pkg for pkg in prompt_packages if pkg.always and pkg.sop_text]
        summary_packages = [pkg for pkg in prompt_packages if not pkg.always]

        if always_packages:
            parts.append(
                "\n\n".join(
                    f"## 常驻技能：{pkg.name}\n{pkg.sop_text}"
                    for pkg in always_packages
                )
            )

        if summary_packages:
            summary_lines = [
                "## 已启用技能摘要",
                "需要使用某个技能时，请先定位对应 `SKILL.md`：",
            ]
            summary_lines.extend(
                self._policy.build_skill_summary_line(pkg, run_type=rt)
                for pkg in summary_packages
            )
            parts.append("\n".join(summary_lines))

        return "\n\n".join(part for part in parts if part.strip())
