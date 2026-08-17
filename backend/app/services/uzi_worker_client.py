"""UZI Worker 内部 API 客户端（文档 §11）。

主服务通过内部 HTTP + 共享密钥调用 Worker 的 ``/internal/*`` 接口。
所有请求携带 ``X-Aniu-Uzi-Token`` 头，与 Worker 的 ``UZI_WORKER_TOKEN``
一致。

安全边界（§8 / §13）：

- **绝不**把 LLM API Key 传给 Worker；LLM 调用只发生在主服务。
- MX API Key 仅在内存中通过 Stage 1 请求体临时传递（Worker 侧脱敏日志）。
- Worker 不可达时返回结构化结果，不向调用方抛异常（由报告服务决策）。
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0

# 稳定错误码（文档 §17.3）
UZI_WORKER_UNAVAILABLE = "UZI_WORKER_UNAVAILABLE"


class WorkerJobPayloadError(RuntimeError):
    """Worker 返回的业务级错误（携带稳定错误码）。"""

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code or "UZI_WORKER_UNAVAILABLE"


class UziWorkerClient:
    def __init__(self) -> None:
        self._http: httpx.Client | None = None

    def _settings(self):
        return get_settings()

    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=_DEFAULT_TIMEOUT_SECONDS)
        return self._http

    def close(self) -> None:
        if self._http is not None:
            try:
                self._http.close()
            except Exception:  # pragma: no cover - defensive
                pass
            self._http = None

    # ── 内部工具 ──────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        settings = self._settings()
        secret = str(settings.uzi_worker_shared_secret or "").strip()
        headers = {"X-Aniu-Uzi-Token": secret}
        return headers

    def _worker_base_url(self) -> str:
        settings = self._settings()
        return str(settings.uzi_worker_url or "").rstrip("/")

    def _enabled(self) -> bool:
        settings = self._settings()
        return bool(settings.uzi_enabled)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any] | None:
        """发送内部请求；Worker 不可达时返回 None（不抛异常）。"""
        if not self._enabled():
            return None
        base = self._worker_base_url()
        if not base:
            return None
        try:
            response = self._client().request(
                method,
                f"{base}{path}",
                headers=self._headers(),
                json=json_body,
                timeout=(
                    timeout_seconds
                    if timeout_seconds is not None
                    else _DEFAULT_TIMEOUT_SECONDS
                ),
            )
        except httpx.HTTPError as exc:
            logger.warning("UZI Worker 不可达: %s %s -> %s", method, path, exc)
            return None

        try:
            payload = response.json()
        except Exception:  # noqa: BLE001 - 非 JSON 响应按不可用处理
            payload = None

        if response.status_code >= 400:
            detail = ""
            if isinstance(payload, dict):
                detail = str(payload.get("detail") or "")
            raise WorkerJobPayloadError(
                detail or f"Worker 返回错误: {response.status_code}",
                error_code=None,
            )
        return payload if isinstance(payload, dict) else {}

    # ── 接口（§11）────────────────────────────────────────
    def health(self) -> dict[str, Any] | None:
        """GET /internal/health（Token 豁免，但按部署可用性检查）。"""
        try:
            payload = self._request("GET", "/internal/health")
        except WorkerJobPayloadError as exc:
            logger.warning("UZI Worker 健康检查失败: %s", exc)
            return None
        if payload is None:
            return None
        return payload

    def submit_stage1(
        self,
        *,
        report_id: int,
        ticker: str,
        report_rel_dir: str,
        mx_api_key: str | None = None,
    ) -> dict[str, Any] | None:
        """POST /internal/jobs/{report_id}/stage1。"""
        return self._request(
            "POST",
            f"/internal/jobs/{report_id}/stage1",
            json_body={
                "ticker": ticker,
                "report_rel_dir": report_rel_dir,
                "mx_api_key": mx_api_key,
            },
        )

    def get_job(self, report_id: int) -> dict[str, Any] | None:
        """GET /internal/jobs/{report_id}。"""
        try:
            payload = self._request("GET", f"/internal/jobs/{report_id}")
        except WorkerJobPayloadError as exc:
            logger.warning("查询 UZI Worker 任务失败: report_id=%s %s", report_id, exc)
            return None
        if payload is None:
            return None
        job = payload.get("job")
        return job if isinstance(job, dict) else None

    def submit_stage2(self, *, report_id: int, ticker: str | None = None) -> dict[str, Any] | None:
        """POST /internal/jobs/{report_id}/stage2。"""
        return self._request(
            "POST",
            f"/internal/jobs/{report_id}/stage2",
            json_body={"ticker": ticker} if ticker else None,
        )

    def cancel(self, report_id: int) -> dict[str, Any] | None:
        """POST /internal/jobs/{report_id}/cancel。"""
        try:
            payload = self._request("POST", f"/internal/jobs/{report_id}/cancel")
        except WorkerJobPayloadError as exc:
            logger.warning("取消 UZI Worker 任务失败: report_id=%s %s", report_id, exc)
            return None
        if payload is None:
            return None
        job = payload.get("job")
        return job if isinstance(job, dict) else None

    def source_status(self, *, check_latest: bool = False) -> dict[str, Any] | None:
        suffix = "?check_latest=true" if check_latest else ""
        try:
            return self._request("GET", f"/internal/source/status{suffix}")
        except WorkerJobPayloadError as exc:
            logger.warning("查询 UZI 上游版本失败: %s", exc)
            return None

    def update_source(self) -> dict[str, Any] | None:
        """检查并原子更新 Worker 上游源码；下载允许最多三分钟。"""
        return self._request(
            "POST",
            "/internal/source/update",
            timeout_seconds=180.0,
        )


uzi_worker_client = UziWorkerClient()
