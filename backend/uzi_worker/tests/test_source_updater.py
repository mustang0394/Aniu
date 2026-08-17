"""UZI 上游源码更新的离线校验与原子切换测试。"""
from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from app.source_updater import SourceUpdateError, UziSourceManager


def _write_source(root: Path, *, marker: str) -> None:
    entry = root / "skills" / "deep-analysis" / "scripts" / "run_real_test.py"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text(
        f"MARKER = {marker!r}\ndef stage1(ticker):\n    return {{}}\n"
        "def stage2(ticker):\n    return ''\n",
        encoding="utf-8",
    )


def _archive(
    commit: str,
    *,
    compatible: bool = True,
    requirements: str | None = None,
) -> bytes:
    prefix = f"UZI-Skill-{commit}"
    entry = (
        "def stage1(ticker):\n    return {}\n"
        + ("def stage2(ticker):\n    return ''\n" if compatible else "")
        + f"MARKER = {commit!r}\n"
    ).encode()
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as bundle:
        info = tarfile.TarInfo(
            f"{prefix}/skills/deep-analysis/scripts/run_real_test.py"
        )
        info.size = len(entry)
        bundle.addfile(info, io.BytesIO(entry))
        if requirements is not None:
            requirement_bytes = requirements.encode()
            req_info = tarfile.TarInfo(f"{prefix}/requirements.txt")
            req_info.size = len(requirement_bytes)
            bundle.addfile(req_info, io.BytesIO(requirement_bytes))
    return output.getvalue()


def _manager(tmp_path: Path, old_commit: str) -> UziSourceManager:
    source = tmp_path / "persistent" / "uzi_source"
    bundled = tmp_path / "bundled"
    _write_source(source, marker="old")
    _write_source(bundled, marker="bundled")
    lock = tmp_path / "uzi-source.lock"
    lock.write_text(json.dumps({"commit": old_commit, "sha256": "old"}), encoding="utf-8")
    return UziSourceManager(
        source_root=source,
        bundled_source_root=bundled,
        bundled_lock_path=lock,
    )


def test_update_switches_to_validated_latest_source(tmp_path, monkeypatch) -> None:
    old_commit = "1" * 40
    latest = "2" * 40
    manager = _manager(tmp_path, old_commit)
    monkeypatch.setattr(manager, "_fetch_latest_commit", lambda: latest)
    monkeypatch.setattr(manager, "_download_archive", lambda _commit: _archive(latest))

    result = manager.update_to_latest()

    assert result["updated"] is True
    assert result["current_commit"] == latest
    assert manager.current_commit() == latest
    entry = manager.source_root / "skills/deep-analysis/scripts/run_real_test.py"
    assert latest in entry.read_text(encoding="utf-8")
    assert not (manager.source_root.parent / ".uzi_source.previous").exists()


def test_incompatible_latest_keeps_current_source(tmp_path, monkeypatch) -> None:
    old_commit = "3" * 40
    latest = "4" * 40
    manager = _manager(tmp_path, old_commit)
    before = (
        manager.source_root / "skills/deep-analysis/scripts/run_real_test.py"
    ).read_text(encoding="utf-8")
    monkeypatch.setattr(manager, "_fetch_latest_commit", lambda: latest)
    monkeypatch.setattr(
        manager,
        "_download_archive",
        lambda _commit: _archive(latest, compatible=False),
    )

    with pytest.raises(SourceUpdateError, match="缺少入口"):
        manager.update_to_latest()

    after = (
        manager.source_root / "skills/deep-analysis/scripts/run_real_test.py"
    ).read_text(encoding="utf-8")
    assert after == before
    assert manager.current_commit() == old_commit


def test_bootstrap_copies_bundled_source_to_persistent_directory(tmp_path) -> None:
    source = tmp_path / "persistent" / "uzi_source"
    bundled = tmp_path / "bundled"
    _write_source(bundled, marker="bundled")
    commit = "5" * 40
    lock = tmp_path / "uzi-source.lock"
    lock.write_text(json.dumps({"commit": commit, "sha256": "digest"}), encoding="utf-8")
    manager = UziSourceManager(
        source_root=source,
        bundled_source_root=bundled,
        bundled_lock_path=lock,
    )

    manager.ensure_initialized()

    assert manager.current_commit() == commit
    assert (source / "skills/deep-analysis/scripts/run_real_test.py").is_file()


def test_dependency_change_is_rejected_without_runtime_install(tmp_path, monkeypatch) -> None:
    old_commit = "6" * 40
    latest = "7" * 40
    manager = _manager(tmp_path, old_commit)
    monkeypatch.setattr(manager, "_fetch_latest_commit", lambda: latest)
    monkeypatch.setattr(
        manager,
        "_download_archive",
        lambda _commit: _archive(latest, requirements="new-package>=1\n"),
    )

    with pytest.raises(SourceUpdateError, match="更新 Worker 镜像"):
        manager.update_to_latest()

    assert manager.current_commit() == old_commit
