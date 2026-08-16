"""日志与错误信息脱敏工具。

要求（文档 §16.3）：日志不得包含 Authorization、LLM Key、MX Key、
Worker Token；API 返回的错误信息不包含宿主机绝对路径与完整环境信息。

实现方式：

- ``register_secret`` / ``unregister_secret`` 维护一组需要替换的敏感值
 （Worker Token、MX Key 等）。
- ``sanitize_text`` 对任意文本做替换。
- ``SanitizingFilter`` 挂到 logging 根记录器，任何日志记录输出前脱敏。
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

_MASK = "***"
_secrets: set[str] = set()
_secrets_lock = threading.Lock()


def register_secret(value: Any) -> None:
    """登记一个需要脱敏的敏感值（仅内存，不落盘）。"""
    text = str(value or "").strip()
    if not text or len(text) < 4:
        return
    with _secrets_lock:
        _secrets.add(text)


def unregister_secret(value: Any) -> None:
    text = str(value or "").strip()
    if not text:
        return
    with _secrets_lock:
        _secrets.discard(text)


def registered_secrets() -> set[str]:
    with _secrets_lock:
        return set(_secrets)


def sanitize_text(text: Any) -> str:
    """将文本中出现的敏感值替换为掩码。空输入原样返回。"""
    if text is None:
        return ""
    output = str(text)
    if not output:
        return output
    with _secrets_lock:
        for secret in _secrets:
            if secret in output:
                output = output.replace(secret, _MASK)
    return output


def _sanitize_args(args: tuple[Any, ...]) -> tuple[Any, ...]:
    cleaned: list[Any] = []
    for item in args:
        if isinstance(item, str):
            cleaned.append(sanitize_text(item))
        elif isinstance(item, BaseException):
            cleaned.append(sanitize_text(str(item)))
        else:
            cleaned.append(item)
    return tuple(cleaned)


class SanitizingFilter(logging.Filter):
    """日志过滤器：记录输出前替换消息与参数中的敏感值（兜底）。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            if getattr(record, "_aniu_sanitized", False):
                return True
            record.msg = sanitize_text(record.msg)
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {
                        key: sanitize_text(value) if isinstance(value, str) else value
                        for key, value in record.args.items()
                    }
                elif isinstance(record.args, tuple):
                    record.args = _sanitize_args(record.args)
            record._aniu_sanitized = True
        except Exception:  # noqa: BLE001 - 脱敏失败绝不阻断日志
            pass
        return True


_filter_installed = False
_filter_lock = threading.Lock()
_original_make_record = None


def install_sanitizing_filter() -> None:
    """全局日志脱敏：在 ``Logger.makeRecord`` 层拦截，覆盖全部 logger
    （包括未来创建的 uvicorn / 子记录器），幂等。"""
    global _filter_installed, _original_make_record
    with _filter_lock:
        if _filter_installed:
            return

        _original_make_record = logging.Logger.makeRecord

        def _sanitizing_make_record(
            self: logging.Logger, *args: Any, **kwargs: Any
        ) -> logging.LogRecord:
            record = _original_make_record(self, *args, **kwargs)  # type: ignore[arg-type]
            if not getattr(record, "_aniu_sanitized", False):
                try:
                    record.msg = sanitize_text(record.msg)
                    if record.args:
                        if isinstance(record.args, dict):
                            record.args = {
                                key: sanitize_text(value)
                                if isinstance(value, str)
                                else value
                                for key, value in record.args.items()
                            }
                        elif isinstance(record.args, tuple):
                            record.args = _sanitize_args(record.args)
                    record._aniu_sanitized = True
                except Exception:  # noqa: BLE001
                    pass
            return record

        logging.Logger.makeRecord = _sanitizing_make_record
        root = logging.getLogger()
        if not any(isinstance(f, SanitizingFilter) for f in root.filters):
            root.addFilter(SanitizingFilter())
        _filter_installed = True


def sanitize_error_message(message: str, *, report_root: Path | None = None) -> str:
    """对外错误消息脱敏：替换敏感值，并把宿主机绝对路径缩略为占位符。"""
    text = sanitize_text(message)
    if report_root is not None:
        try:
            text = text.replace(str(report_root.resolve()), "{UZI_REPORT_ROOT}")
        except Exception:  # noqa: BLE001
            pass
    return text