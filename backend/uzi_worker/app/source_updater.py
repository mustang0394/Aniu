"""UZI 上游源码的受控检查、校验与原子更新。"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import logging
import os
import shutil
import tarfile
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

logger = logging.getLogger(__name__)

UPSTREAM_REPOSITORY = "https://github.com/wbh604/UZI-Skill"
_LATEST_COMMIT_API = "https://api.github.com/repos/wbh604/UZI-Skill/commits?per_page=1"
_ARCHIVE_URL = "https://github.com/wbh604/UZI-Skill/archive/{commit}.tar.gz"
_SOURCE_META_FILE = ".aniu-uzi-source.json"
_REQUIRED_ENTRY = Path("skills/deep-analysis/scripts/run_real_test.py")
_MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
_MAX_EXTRACTED_BYTES = 300 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 20_000


class SourceUpdateError(RuntimeError):
    """上游检查或更新失败。"""


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class UziSourceManager:
    def __init__(
        self,
        *,
        source_root: Path,
        bundled_source_root: Path,
        bundled_lock_path: Path,
    ) -> None:
        self.source_root = Path(source_root)
        self.bundled_source_root = Path(bundled_source_root)
        self.bundled_lock_path = Path(bundled_lock_path)
        self._lock = threading.Lock()

    def ensure_initialized(self) -> None:
        """首次启动时把镜像内固定版本复制到持久源码目录。"""
        if self._source_is_compatible(self.source_root):
            return
        if self.source_root.resolve() == self.bundled_source_root.resolve():
            raise SourceUpdateError(f"UZI 源码入口缺失: {self.source_root}")
        if not self._source_is_compatible(self.bundled_source_root):
            raise SourceUpdateError(f"镜像内 UZI 源码入口缺失: {self.bundled_source_root}")

        self.source_root.parent.mkdir(parents=True, exist_ok=True)
        temp_path = Path(
            tempfile.mkdtemp(
                prefix=f".{self.source_root.name}.bootstrap-",
                dir=str(self.source_root.parent),
            )
        )
        candidate = temp_path / "source"
        backup = self.source_root.parent / f".{self.source_root.name}.invalid"
        try:
            shutil.copytree(self.bundled_source_root, candidate)
            lock = self._read_json(self.bundled_lock_path)
            commit = str(lock.get("commit") or "").strip()
            self._write_metadata(candidate, commit=commit, sha256=str(lock.get("sha256") or ""))
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            if self.source_root.exists():
                os.replace(self.source_root, backup)
            try:
                os.replace(candidate, self.source_root)
            except Exception:
                if backup.exists() and not self.source_root.exists():
                    os.replace(backup, self.source_root)
                raise
            shutil.rmtree(backup, ignore_errors=True)
        finally:
            shutil.rmtree(temp_path, ignore_errors=True)
        logger.info("已初始化持久 UZI 源码: commit=%s", self.current_commit())

    def current_commit(self) -> str:
        metadata = self._read_json(self.source_root / _SOURCE_META_FILE)
        commit = str(metadata.get("commit") or "").strip()
        if commit:
            return commit
        lock = self._read_json(self.bundled_lock_path)
        return str(lock.get("commit") or "unknown").strip() or "unknown"

    def is_ready(self) -> bool:
        return self._source_is_compatible(self.source_root)

    def status(self, *, check_latest: bool = False) -> dict[str, Any]:
        current = self.current_commit()
        latest: str | None = None
        error: str | None = None
        if check_latest:
            try:
                latest = self._fetch_latest_commit()
            except SourceUpdateError as exc:
                error = str(exc)
        return self._status_payload(
            current=current,
            latest=latest,
            error=error,
            updated=False,
        )

    def update_to_latest(self) -> dict[str, Any]:
        """下载最新提交，校验后原子替换当前源码。"""
        with self._lock:
            current = self.current_commit()
            latest = self._fetch_latest_commit()
            if current == latest:
                payload = self._status_payload(
                    current=current,
                    latest=latest,
                    updated=False,
                )
                payload["message"] = "当前已经是上游最新版本。"
                return payload

            archive = self._download_archive(latest)
            digest = hashlib.sha256(archive).hexdigest()
            self.source_root.parent.mkdir(parents=True, exist_ok=True)
            temp_path = Path(
                tempfile.mkdtemp(
                    prefix=f".{self.source_root.name}.update-",
                    dir=str(self.source_root.parent),
                )
            )
            backup = self.source_root.parent / f".{self.source_root.name}.previous"
            candidate: Path | None = None
            try:
                candidate = self._extract_archive(archive, temp_path)
                self._validate_source(candidate)
                self._validate_dependency_contract(candidate)
                self._write_metadata(candidate, commit=latest, sha256=digest)

                if backup.exists():
                    shutil.rmtree(backup, ignore_errors=True)
                if self.source_root.exists():
                    os.replace(self.source_root, backup)
                try:
                    os.replace(candidate, self.source_root)
                except Exception:
                    if backup.exists() and not self.source_root.exists():
                        os.replace(backup, self.source_root)
                    raise
                shutil.rmtree(backup, ignore_errors=True)
            except SourceUpdateError:
                raise
            except Exception as exc:  # noqa: BLE001 - 转为稳定更新错误
                raise SourceUpdateError(f"UZI 源码切换失败：{exc}") from exc
            finally:
                shutil.rmtree(temp_path, ignore_errors=True)

            payload = self._status_payload(
                current=latest,
                latest=latest,
                updated=True,
            )
            payload["message"] = "UZI 上游源码已更新，新任务将使用新版本。"
            logger.info("UZI 上游源码已更新: %s -> %s", current, latest)
            return payload

    def _fetch_latest_commit(self) -> str:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - production lock includes httpx
            raise SourceUpdateError("Worker 缺少 httpx，无法检查 UZI 上游版本。") from exc
        try:
            response = httpx.get(
                _LATEST_COMMIT_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "Aniu-UZI-Worker",
                },
                timeout=15.0,
                follow_redirects=True,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SourceUpdateError(f"无法检查 UZI 上游版本：{exc}") from exc
        if not isinstance(payload, list) or not payload:
            raise SourceUpdateError("UZI 上游版本接口返回为空。")
        commit = str(payload[0].get("sha") or "").strip()
        if len(commit) != 40 or any(ch not in "0123456789abcdef" for ch in commit.lower()):
            raise SourceUpdateError("UZI 上游返回了非法 commit。")
        return commit.lower()

    def _download_archive(self, commit: str) -> bytes:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - production lock includes httpx
            raise SourceUpdateError("Worker 缺少 httpx，无法下载 UZI 上游归档。") from exc
        url = _ARCHIVE_URL.format(commit=commit)
        chunks: list[bytes] = []
        total = 0
        try:
            with httpx.stream(
                "GET",
                url,
                headers={"User-Agent": "Aniu-UZI-Worker"},
                timeout=httpx.Timeout(60.0, connect=15.0),
                follow_redirects=True,
            ) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > _MAX_ARCHIVE_BYTES:
                        raise SourceUpdateError("UZI 上游归档超过 100MB 安全上限。")
                    chunks.append(chunk)
        except SourceUpdateError:
            raise
        except httpx.HTTPError as exc:
            raise SourceUpdateError(f"下载 UZI 上游归档失败：{exc}") from exc
        if not chunks:
            raise SourceUpdateError("下载到的 UZI 上游归档为空。")
        return b"".join(chunks)

    def _extract_archive(self, archive: bytes, destination: Path) -> Path:
        extracted_root = destination / "extracted"
        extracted_root.mkdir(parents=True, exist_ok=True)
        top_levels: set[str] = set()
        extracted_size = 0
        try:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
                members = bundle.getmembers()
                if len(members) > _MAX_ARCHIVE_MEMBERS:
                    raise SourceUpdateError("UZI 上游归档文件数超过安全上限。")
                for member in members:
                    pure = PurePosixPath(member.name)
                    parts = pure.parts
                    if (
                        not parts
                        or pure.is_absolute()
                        or ".." in parts
                        or member.issym()
                        or member.islnk()
                        or member.isdev()
                    ):
                        raise SourceUpdateError("UZI 上游归档包含不安全路径或链接。")
                    top_levels.add(parts[0])
                    target = extracted_root.joinpath(*parts)
                    if member.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    if not member.isfile():
                        continue
                    extracted_size += max(0, int(member.size))
                    if extracted_size > _MAX_EXTRACTED_BYTES:
                        raise SourceUpdateError("UZI 上游解压内容超过安全上限。")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    source = bundle.extractfile(member)
                    if source is None:
                        raise SourceUpdateError("UZI 上游归档存在不可读取文件。")
                    with source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        except SourceUpdateError:
            raise
        except (tarfile.TarError, OSError) as exc:
            raise SourceUpdateError(f"UZI 上游归档解压失败：{exc}") from exc
        if len(top_levels) != 1:
            raise SourceUpdateError("UZI 上游归档目录结构不符合预期。")
        return extracted_root / next(iter(top_levels))

    def _validate_source(self, source_root: Path) -> None:
        entry = source_root / _REQUIRED_ENTRY
        if not entry.is_file():
            raise SourceUpdateError("UZI 新版本缺少 run_real_test.py 入口。")
        try:
            tree = ast.parse(entry.read_text(encoding="utf-8"), filename=str(entry))
        except (OSError, SyntaxError, UnicodeError) as exc:
            raise SourceUpdateError(f"UZI 新版本入口无法解析：{exc}") from exc
        functions = {
            node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        missing = {"stage1", "stage2"} - functions
        if missing:
            raise SourceUpdateError(
                "UZI 新版本与 AniU 协议不兼容，缺少入口：" + ", ".join(sorted(missing))
            )

    def _source_is_compatible(self, source_root: Path) -> bool:
        try:
            self._validate_source(source_root)
        except SourceUpdateError:
            return False
        return True

    def _validate_dependency_contract(self, candidate: Path) -> None:
        """运行时不安装依赖；依赖声明变化时要求先发布新 Worker 镜像。"""
        bundled_files = self._dependency_files(self.bundled_source_root)
        candidate_files = self._dependency_files(candidate)
        if set(bundled_files) != set(candidate_files):
            raise SourceUpdateError(
                "UZI 新版本修改了依赖文件，需要先更新 Worker 镜像，已保留当前版本。"
            )
        for relative_path, bundled_content in bundled_files.items():
            if candidate_files[relative_path] != bundled_content:
                raise SourceUpdateError(
                    "UZI 新版本修改了依赖声明，需要先更新 Worker 镜像，已保留当前版本。"
                )

    @staticmethod
    def _dependency_files(source_root: Path) -> dict[str, bytes]:
        candidates = list(source_root.glob("requirements*.txt"))
        deep_root = source_root / "skills" / "deep-analysis"
        if deep_root.is_dir():
            candidates.extend(deep_root.rglob("requirements*.txt"))
        result: dict[str, bytes] = {}
        for path in candidates:
            if not path.is_file():
                continue
            try:
                relative = path.relative_to(source_root).as_posix()
                result[relative] = path.read_bytes()
            except OSError as exc:
                raise SourceUpdateError(f"无法读取 UZI 依赖声明：{exc}") from exc
        return result

    def _write_metadata(self, source_root: Path, *, commit: str, sha256: str) -> None:
        payload = {
            "repository": UPSTREAM_REPOSITORY,
            "commit": commit,
            "sha256": sha256,
            "installed_at": _utc_now_iso(),
        }
        (source_root / _SOURCE_META_FILE).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _status_payload(
        *,
        current: str,
        latest: str | None,
        updated: bool,
        error: str | None = None,
    ) -> dict[str, Any]:
        return {
            "repository": UPSTREAM_REPOSITORY,
            "current_commit": current,
            "current_version": current[:7] if current != "unknown" else current,
            "latest_commit": latest,
            "latest_version": latest[:7] if latest else None,
            "update_available": bool(latest and current != latest),
            "updated": updated,
            "checked_at": _utc_now_iso(),
            "error": error,
        }
