"""Thin facade that exposes the skill catalog and runtime through one object."""
from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any

from app.skills.catalog import SkillCatalog
from app.skills.loader import SkillPackage
from app.skills.policy import SkillPolicy
from app.skills.runtime import SkillRuntime


class SkillRegistry:
    def __init__(self) -> None:
        self._policy = SkillPolicy()
        self._catalog = SkillCatalog(policy=self._policy)
        self._runtime = SkillRuntime(catalog=self._catalog, policy=self._policy)

    def ensure_loaded(self) -> None:
        self._catalog.ensure_loaded()

    def reload(
        self,
        builtin_dir: Path | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self._catalog.reload(builtin_dir=builtin_dir, workspace_dir=workspace_dir)

    def is_system_runtime(self, skill_id: str | SkillPackage) -> bool:
        target = skill_id if isinstance(skill_id, SkillPackage) else self._catalog.find_package(str(skill_id))
        return target is not None and self._policy.is_system_runtime(target)

    def set_disabled(self, skill_ids: set[str]) -> None:
        self._catalog.set_disabled(skill_ids)

    def enabled_packages(self) -> list[SkillPackage]:
        return self._catalog.enabled_packages()

    def all_packages(self) -> list[SkillPackage]:
        return self._catalog.all_packages()

    def build_tools(
        self,
        *,
        run_type: str | None = None,
        disabled_skill_ids: Collection[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self._runtime.build_tools(
            run_type=run_type, disabled_skill_ids=disabled_skill_ids
        )

    def execute_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        context: dict[str, Any],
        disabled_skill_ids: Collection[str] | None = None,
    ) -> dict[str, Any]:
        return self._runtime.execute_tool(
            tool_name=tool_name,
            arguments=arguments,
            context=context,
            disabled_skill_ids=disabled_skill_ids,
        )

    def build_prompt_supplement(
        self,
        *,
        run_type: str | None = None,
        disabled_skill_ids: Collection[str] | None = None,
    ) -> str:
        return self._runtime.build_prompt_supplement(
            run_type=run_type, disabled_skill_ids=disabled_skill_ids
        )

    def list_skill_info(self) -> list[dict[str, Any]]:
        return [
            pkg.to_info(enabled=self._catalog.is_enabled(pkg))
            for pkg in self._catalog.all_packages()
        ]


skill_registry = SkillRegistry()
