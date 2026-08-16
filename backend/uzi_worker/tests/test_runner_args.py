"""Worker runner 命令行参数构建测试（回归 --no-mock bug）。

确保非 mock 模式下构建的 worker_child 命令不传无效的 --no-mock 参数，
argparse 能正常解析。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WORKER_ROOT = Path(__file__).resolve().parents[1]
if str(_WORKER_ROOT) not in sys.path:
    sys.path.insert(0, str(_WORKER_ROOT))

from app.worker_child import _parse_args


def test_worker_child_accepts_no_mock_flag(mock=False) -> None:
    """非 mock 模式：不传 --mock，argparse 正常解析，mock 默认 False。"""
    argv = [
        "--report-id", "1",
        "--stage", "1",
        "--ticker", "600519.SH",
        "--report-root", "/tmp/r",
        "--source-root", "/tmp/s",
    ]
    args = _parse_args(argv)
    assert args.mock is False
    assert args.report_id == "1"
    assert args.stage == "1"
    assert args.ticker == "600519.SH"


def test_worker_child_accepts_mock_flag() -> None:
    """mock 模式：传 --mock，argparse 正常解析。"""
    argv = [
        "--report-id", "2",
        "--stage", "2",
        "--ticker", "600519.SH",
        "--report-root", "/tmp/r",
        "--source-root", "/tmp/s",
        "--mock",
    ]
    args = _parse_args(argv)
    assert args.mock is True


