"""日志脱敏测试（§20.2：日志脱敏 MX Key 和内部 Token；§16.3）。"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from app.sanitize import (
    SanitizingFilter,
    install_sanitizing_filter,
    register_secret,
    sanitize_error_message,
    sanitize_text,
    unregister_secret,
)


def test_sanitize_text_masks_secrets():
    register_secret("supersecret-mx-key")
    register_secret("worker-token-abc")
    try:
        assert "supersecret-mx-key" not in sanitize_text(
            "调用失败: supersecret-mx-key 无效"
        )
        assert "worker-token-abc" not in sanitize_text(
            "token=worker-token-abc"
        )
        assert "***" in sanitize_text("token=worker-token-abc")
        # 未登记的普通文本不受影响。
        assert sanitize_text("普通日志文本") == "普通日志文本"
    finally:
        unregister_secret("supersecret-mx-key")
        unregister_secret("worker-token-abc")


def test_logging_filter_sanitizes_records():
    register_secret("mx-secret-key-42")
    try:
        install_sanitizing_filter()
        logger = logging.getLogger("uzi.test_sanitize")
        captured: list[logging.LogRecord] = []

        class _Handler(logging.Handler):
            def emit(self, record):
                captured.append(record)

        handler = _Handler()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            logger.info("查询失败: %s", "mx-secret-key-42")
            logger.error("task %s failed with token %s", "123", "mx-secret-key-42")
        finally:
            logger.removeHandler(handler)

        assert captured, "应捕获到日志记录"
        for record in captured:
            assert "mx-secret-key-42" not in record.getMessage()
    finally:
        unregister_secret("mx-secret-key-42")


def test_error_message_sanitizes_absolute_path(tmp_path):
    register_secret("leaked-worker-token")
    try:
        message = f"错误发生在 {tmp_path}/reports/1/work，token leaked-worker-token"
        cleaned = sanitize_error_message(message, report_root=tmp_path / "reports")
        assert str(tmp_path) not in cleaned
        assert "leaked-worker-token" not in cleaned
        assert "{UZI_REPORT_ROOT}" in cleaned
        assert "***" in cleaned
    finally:
        unregister_secret("leaked-worker-token")


def test_mx_key_only_in_memory_env(worker_env, worker_client, auth_headers, monkeypatch):
    """MX Key 通过内存环境变量传递，不落盘、不出现在状态文件。"""
    monkeypatch.setenv("UZI_MOCK_SLEEP_SECONDS", "1")

    report_root = Path(worker_env["report_root"])
    secret = "mx-key-for-log-sanitize-test-8888"

    response = worker_client.post(
        "/internal/jobs/1001/stage1",
        json={"ticker": "600519.SH", "report_rel_dir": "1001", "mx_api_key": secret},
        headers=auth_headers,
    )
    assert response.status_code == 202

    # 等待任务结束（足够长以观察日志）。
    job = {}
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = worker_client.get("/internal/jobs/1001", headers=auth_headers).json()["job"]
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            break
        time.sleep(0.3)

    # 状态文件、清单与产物中都不允许出现 MX Key。
    for path in [report_root / "1001" / "worker-state.json"]:
        if path.exists():
            assert secret not in path.read_text(encoding="utf-8")

    # 日志文件脱敏（stage1.log 内容不含明文 Key）。
    log_paths = (report_root / "1001" / "logs").glob("*.log")
    for log_path in log_paths:
        content = log_path.read_text(encoding="utf-8", errors="replace")
        assert secret not in content, f"{log_path} 泄露了 MX Key"

    # mock 子进程不会把 key 写日志；验证状态流转正常即可。
    assert job["status"] == "succeeded"