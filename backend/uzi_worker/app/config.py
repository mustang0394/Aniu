"""Worker 配置：全部来自环境变量（部署基础设施，不进入 AppSettings UI）。

敏感信息要求（文档 §8）：

- ``UZI_WORKER_TOKEN`` 不允许提供默认弱值；未配置时除健康检查外的
  所有内部接口一律拒绝。
- MX API Key 仅通过"内存 + 子进程环境变量"短期传递，并在日志过滤器中脱敏。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_WORKER_PORT = 9001
DEFAULT_SOURCE_ROOT = "/opt/uzi"
DEFAULT_BUNDLED_SOURCE_ROOT = "/opt/uzi"
DEFAULT_REPORT_ROOT = "/app/data/uzi_reports"
DEFAULT_STAGE2_TIMEOUT_SECONDS = 600
DEFAULT_RENDER_TIMEOUT_SECONDS = 90
DEFAULT_QUANT_MAX_FUNDS = 12
DEFAULT_QUANT_TIMEOUT_SECONDS = 45

# 固定的 UZI 深度模式（文档 §2.2：不提供深度级别选择，只支持 deep）。
FIXED_DEPTH = "deep"
FIXED_LITE = "0"
FIXED_NO_AUTO_OPEN = "1"
FIXED_PLAYWRIGHT_ENABLE = "1"


@dataclass(frozen=True)
class WorkerConfig:
    token: str | None
    source_root: Path
    bundled_source_root: Path
    report_root: Path
    port: int
    mock: bool
    stage2_timeout_seconds: int
    render_timeout_seconds: int
    quant_max_funds: int
    quant_timeout_seconds: int


def _env_or(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip()


def _env_path(name: str, default: str) -> Path:
    raw = _env_or(name, default) or default
    return Path(raw)


def _env_positive_int(name: str, default: int) -> int:
    raw = _env_or(name, str(default)) or str(default)
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def get_worker_config() -> WorkerConfig:
    """每次调用都重新读取环境变量，便于测试与部署调整。"""
    token = _env_or("UZI_WORKER_TOKEN")
    return WorkerConfig(
        token=token or None,
        source_root=_env_path("UZI_SOURCE_ROOT", DEFAULT_SOURCE_ROOT),
        bundled_source_root=_env_path(
            "UZI_BUNDLED_SOURCE_ROOT", DEFAULT_BUNDLED_SOURCE_ROOT
        ),
        report_root=_env_path("UZI_REPORT_ROOT", DEFAULT_REPORT_ROOT),
        port=int(_env_or("UZI_WORKER_PORT", str(DEFAULT_WORKER_PORT)) or DEFAULT_WORKER_PORT),
        mock=_env_or("UZI_WORKER_MOCK", "0") in {"1", "true", "True", "yes"},
        stage2_timeout_seconds=_env_positive_int(
            "UZI_STAGE2_TIMEOUT_SECONDS", DEFAULT_STAGE2_TIMEOUT_SECONDS
        ),
        render_timeout_seconds=_env_positive_int(
            "UZI_RENDER_TIMEOUT_SECONDS", DEFAULT_RENDER_TIMEOUT_SECONDS
        ),
        quant_max_funds=_env_positive_int(
            "UZI_QUANT_MAX_FUNDS", DEFAULT_QUANT_MAX_FUNDS
        ),
        quant_timeout_seconds=_env_positive_int(
            "UZI_QUANT_TIMEOUT_SECONDS", DEFAULT_QUANT_TIMEOUT_SECONDS
        ),
    )


def get_stage_env() -> dict[str, str]:
    """UZI 运行所需的固定环境变量（文档 §8 Worker 配置表）。"""
    return {
        "UZI_DEPTH": FIXED_DEPTH,
        "UZI_LITE": FIXED_LITE,
        "UZI_NO_AUTO_OPEN": FIXED_NO_AUTO_OPEN,
        "UZI_PLAYWRIGHT_ENABLE": FIXED_PLAYWRIGHT_ENABLE,
        "PYTHONUNBUFFERED": "1",
    }
