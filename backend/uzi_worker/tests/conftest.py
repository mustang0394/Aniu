"""Worker 测试公共夹具。

所有测试使用 mock 模式与独立临时目录，不依赖真实 UZI 源码、
网络或 Chromium（文档 §19.2 / §20.2）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# 确保测试可以导入 worker 的 app 包（backend/uzi_worker/）。
_WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKER_ROOT))


@pytest.fixture()
def worker_env(tmp_path, monkeypatch):
    """隔离环境：独立报告目录、固定 Token、mock 模式。"""
    report_root = tmp_path / "reports"
    source_root = tmp_path / "uzi-src"
    monkeypatch.setenv("UZI_WORKER_TOKEN", "test-worker-token-123")
    monkeypatch.setenv("UZI_REPORT_ROOT", str(report_root))
    monkeypatch.setenv("UZI_SOURCE_ROOT", str(source_root))
    monkeypatch.setenv("UZI_WORKER_MOCK", "1")
    monkeypatch.setenv("UZI_WORKER_PORT", "9001")
    return {"report_root": report_root, "source_root": source_root, "tmp_path": tmp_path}


@pytest.fixture()
def worker_client(worker_env):
    """FastAPI TestClient（启用 lifespan，启动 runner 监控线程）。"""
    from fastapi.testclient import TestClient

    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        yield client


@pytest.fixture()
def auth_headers():
    return {"X-Aniu-Uzi-Token": "test-worker-token-123"}