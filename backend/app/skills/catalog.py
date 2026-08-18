"""Stateful skill package catalog with discovery and enable/disable state."""
from __future__ import annotations

import threading
from collections.abc import Collection
from pathlib import Path

from app.skills.loader import SkillPackage, discover_skill_packages
from app.skills.policy import SkillPolicy

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_DEFAULT_BUILTIN_DIR = _BACKEND_DIR / "skills"


def _default_workspace_dir() -> Path:
    try:
        from app.core.config import get_skill_workspace_skills_dir, get_settings

        return get_skill_workspace_skills_dir(get_settings())
    except Exception:
        return _BACKEND_DIR / "data" / "skill_workspace" / "skills"


class SkillCatalog:
    def __init__(self, *, policy: SkillPolicy) -> None:
        self._policy = policy
        self._lock = threading.RLock()
        self._packages: list[SkillPackage] = []
        self._disabled: set[str] = set()
        self._loaded = False

    def ensure_loaded(self) -> None:
        with self._lock:
            if self._loaded:
                return
            self.reload()

    def reload(
        self,
        builtin_dir: Path | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        with self._lock:
            self._packages = discover_skill_packages(
                builtin_dir=builtin_dir or _DEFAULT_BUILTIN_DIR,
                workspace_dir=workspace_dir or _default_workspace_dir(),
            )
            self._loaded = True

    def all_packages(self) -> list[SkillPackage]:
        self.ensure_loaded()
        with self._lock:
            return list(self._packages)

    def find_package(self, skill_id: str) -> SkillPackage | None:
        normalized = str(skill_id or "").strip()
        if not normalized:
            return None
        for pkg in self.all_packages():
            if pkg.id == normalized:
                return pkg
        return None

    def set_disabled(self, skill_ids: set[str]) -> None:
        with self._lock:
            self._disabled = {
                skill_id
                for skill_id in skill_ids
                if (pkg := self.find_package(skill_id)) is not None and pkg.can_disable
            }

    def enabled_packages(
        self, extra_disabled: Collection[str] | None = None
    ) -> list[SkillPackage]:
        """全局启用列表 + 额外的账户级禁用集合（只读过滤，不修改全局状态）。"""
        self.ensure_loaded()
        with self._lock:
            disabled = set(self._disabled)
            if extra_disabled:
                disabled.update(extra_disabled)
            return [
                pkg for pkg in self._packages if self._policy.is_enabled(pkg, disabled)
            ]

    def is_enabled(self, pkg: SkillPackage) -> bool:
        self.ensure_loaded()
        with self._lock:
            return self._policy.is_enabled(pkg, set(self._disabled))
