from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

import httpx

from app.skills.providers import build_skill_context
from skills.mx_core.client import MXClient
from skills.mx_core.capital_seal import build_capital_seal_prompt
from skills.mx_core.markets import (
    build_allowed_markets_prompt,
    get_allowed_markets_from_settings,
)
from skills.mx_core.tool_specs import (
    MARKET_QUERY_TOOL_NAMES,
    MUTATION_TOOL_NAMES,
    QUERY_TOOL_NAMES,
)
from app.skills import skill_registry

logger = logging.getLogger(__name__)

_LLM_TEMPERATURE = 0.2
_MAX_TOOL_ITERATIONS = 100
_FINAL_STREAM_CHUNK_SIZE = 96
_DEFAULT_LLM_MAX_RETRIES = 3
_LLM_RETRY_BASE_SECONDS = 1.0
_LLM_RETRY_MAX_SECONDS = 20.0
_LLM_RETRY_CANCEL_POLL_SECONDS = 0.2
_MAX_FRESHNESS_CORRECTIONS = 2
_PREFETCH_TOOL_RESULT_CHAR_LIMIT = 3000
_PREFETCH_SNAPSHOT_CHAR_LIMIT = 12000
_SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
_CHAT_CONFIRMATION_APPEND_PROMPT = (
    "聊天专用安全规则：当操作涉及交易执行、下单、撤单、自选股增删、写入、删除、覆盖、"
    "批量修改或其他会改变数据、文件、配置、状态的破坏性操作时，你必须先明确说明拟执行操作、"
    "影响范围和潜在风险，并在得到用户明确确认后才能调用工具或执行操作；若未获得明确确认，"
    "只能提供方案、预览或建议，不得直接执行。"
)
_TRADE_ENFORCEMENT_PROMPT = (
    "## 交易执行强制规则（trade 模式专属）\n"
    "你当前处于交易（trade）模式，你必须实际执行交易操作，而不是仅仅在文本中讨论。\n"
    "关键规则：\n"
    "1. 当你经过分析判断需要买入某只股票时，必须调用 mx_moni_trade 工具（action=\"BUY\"）\n"
    "2. 当你经过分析判断需要卖出某只股票时，必须调用 mx_moni_trade 工具（action=\"SELL\"）\n"
    "3. 当你经过分析判断需要撤单时，必须调用 mx_moni_cancel 工具\n"
    "4. 即使你判断应该继续持有、不做任何交易操作，也必须先调用 mx_query_market / "
    "mx_search_news / mx_get_positions / mx_get_balance 等查询类工具，或确认系统提供的"
    "[本轮实时数据快照]已成功包含市场、持仓与资金证据，再给出结论。快照失败项必须补查。\n"
    "5. 在文本中说「建议买入」「应该卖出」「可以建仓」等不会触发任何实际操作"
    "——只有工具调用才会执行交易。如果你不调用函数，交易就不会发生。\n"
    "6. 当系统实时快照未齐备而当前仅提供查询工具时，应优先选择查询类工具；不得在未获取"
    "最新行情、持仓、资金数据前直接调用 mx_moni_trade / mx_moni_cancel。"
)
_ANALYSIS_ENFORCEMENT_PROMPT = (
    "## 数据获取强制规则（analysis 模式专属）\n"
    "你当前处于分析（analysis）模式，你的分析结论必须建立在真实数据之上。\n"
    "关键规则：\n"
    "1. 必须先调用查询类工具，或确认系统提供的[本轮实时数据快照]已成功获取最新行情、"
    "资讯、持仓与资金数据，再给出分析结论。\n"
    "2. 严禁在零工具调用且无成功系统预取的前提下直接输出分析结论。\n"
    "3. 当实时快照缺失或失败时，应优先选择当前可见的查询类工具补齐。"
)


_BUSINESS_ERROR_KEYS = frozenset(
    {"error", "errors", "errmsg", "error_msg", "errormsg", "error_message"}
)
_BUSINESS_STATUS_KEYS = frozenset({"status", "state", "状态"})
_BUSINESS_NESTED_KEYS = frozenset(
    {"data", "result", "response", "payload", "body", "rows", "items", "records"}
)
_BUSINESS_ROW_CONTAINER_KEYS = frozenset({"rows", "items", "records"})
_BUSINESS_FAILURE_STATUSES = frozenset(
    {"error", "failed", "fail", "failure", "失败", "错误"}
)
_BUSINESS_FAILURE_MESSAGE_FRAGMENTS = (
    "调用次数已达上限",
    "限流",
    "失败",
    "错误",
)


def _has_meaningful_failure_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized not in {"", "0", "false", "none", "null", "ok", "success"}
    return bool(value)


def _is_explicit_false(value: Any) -> bool:
    if value is False:
        return True
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        return value.strip().lower() in {"0", "false", "no", "失败"}
    return False


def _is_business_failure_status(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    if normalized in _BUSINESS_FAILURE_STATUSES:
        return True
    return (
        normalized.startswith(("error:", "error_", "failed:", "failed_"))
        or "失败" in normalized
        or "错误" in normalized
    )


# Known Miaoxiang/business failure codes. Success responses often carry
# ``code`` values such as 0, 200, "200" or omit it entirely; only treat the
# explicitly documented failure codes (rate limit, business error, negative)
# as failures so real ``mx_get_positions``/``mx_get_balance`` payloads are not
# misclassified as freshness failures.
_BUSINESS_FAILURE_CODE_VALUES: frozenset[Any] = frozenset(
    {113, -1, -113, "113", "-1", "-113"}
)


def _is_business_failure_code(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        if value < 0:
            return True
        if value in _BUSINESS_FAILURE_CODE_VALUES:
            return True
        return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized or normalized in {"0", "200", "ok", "success"}:
            return False
        if normalized in {"113", "-1", "-113"}:
            return True
        return any(
            fragment in normalized
            for fragment in ("error", "fail", "失败", "错误", "限流", "调用次数已达上限")
        )
    return False


def _contains_business_failure(
    value: Any,
    *,
    _check_response_metadata: bool = True,
) -> bool:
    """Inspect a result and its common envelope keys for explicit failures."""
    if isinstance(value, list):
        return any(
            _contains_business_failure(
                item,
                _check_response_metadata=_check_response_metadata,
            )
            for item in value
        )
    if not isinstance(value, dict):
        return False

    for key, item in value.items():
        normalized_key = str(key).strip().lower()
        if (
            normalized_key in _BUSINESS_ERROR_KEYS
            and _has_meaningful_failure_value(item)
        ):
            return True
        if (
            normalized_key in {"failed", "failure"}
            and _has_meaningful_failure_value(item)
        ):
            return True
        if normalized_key in {"ok", "success", "succeeded"} and _is_explicit_false(
            item
        ):
            return True
        if normalized_key in _BUSINESS_STATUS_KEYS and _is_business_failure_status(item):
            return True
        if (
            _check_response_metadata
            and normalized_key == "code"
            and _is_business_failure_code(item)
        ):
            return True
        if _check_response_metadata and normalized_key in {"msg", "message"}:
            message = str(item or "").strip()
            if any(fragment in message for fragment in _BUSINESS_FAILURE_MESSAGE_FRAGMENTS):
                return True

    for key, item in value.items():
        normalized_key = str(key).strip().lower()
        if normalized_key not in _BUSINESS_NESTED_KEYS:
            continue
        if _contains_business_failure(
            item,
            _check_response_metadata=(
                _check_response_metadata
                and normalized_key not in _BUSINESS_ROW_CONTAINER_KEYS
            ),
        ):
            return True
    return False


@dataclass
class FreshnessTracker:
    """Track current-run evidence and whether mutation replay is unsafe."""

    run_type: str
    successful_query_tools: set[str] = field(default_factory=set)
    failed_query_tools: set[str] = field(default_factory=set)
    prefetched_tool_names: set[str] = field(default_factory=set)
    prefetched_successful_query_tools: set[str] = field(default_factory=set)
    prefetched_failed_query_tools: set[str] = field(default_factory=set)
    mutations_executed: list[str] = field(default_factory=list)
    correction_attempts: int = 0
    captured_at: str = ""
    requires_orders: bool = False

    def __post_init__(self) -> None:
        self.run_type = str(self.run_type or "").strip()
        self.prefetched_successful_query_tools.update(self.successful_query_tools)
        self.prefetched_failed_query_tools.update(self.failed_query_tools)

    @staticmethod
    def _is_business_success(tool_result: Any) -> bool:
        if not isinstance(tool_result, dict) or tool_result.get("ok") is not True:
            return False
        result = tool_result.get("result")
        if not isinstance(result, dict) or not result:
            return False
        return not _contains_business_failure(result)

    def record_prefetched_tool_result(
        self, tool_name: str, tool_result: Any
    ) -> None:
        self.prefetched_tool_names.add(tool_name)
        if tool_name not in QUERY_TOOL_NAMES:
            return
        if self._is_business_success(tool_result):
            self.successful_query_tools.add(tool_name)
            self.prefetched_successful_query_tools.add(tool_name)
            self.failed_query_tools.discard(tool_name)
            self.prefetched_failed_query_tools.discard(tool_name)
        else:
            self.failed_query_tools.add(tool_name)
            self.prefetched_failed_query_tools.add(tool_name)

    def seed_from_prefetch(self, tool_calls: Iterable[Any]) -> None:
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("name") or "").strip()
            if not tool_name:
                continue
            self.record_prefetched_tool_result(tool_name, item.get("result"))

    def record_tool_result(self, tool_name: str, tool_result: Any) -> None:
        if tool_name in QUERY_TOOL_NAMES:
            if self._is_business_success(tool_result):
                self.successful_query_tools.add(tool_name)
                self.failed_query_tools.discard(tool_name)
            else:
                self.failed_query_tools.add(tool_name)
        if (
            isinstance(tool_result, dict)
            and bool(tool_result.get("ok"))
            and isinstance(tool_result.get("executed_action"), dict)
        ):
            self.mutations_executed.append(tool_name)

    def reset_for_round_retry(self) -> None:
        """Drop evidence omitted when retry replays only the initial messages."""
        self.successful_query_tools = set(self.prefetched_successful_query_tools)
        self.failed_query_tools = set(self.prefetched_failed_query_tools)
        self.correction_attempts = 0

    def missing_requirements(self) -> list[str]:
        missing: list[str] = []
        if not any(
            name in self.successful_query_tools for name in MARKET_QUERY_TOOL_NAMES
        ):
            missing.append("实时行情或资讯")
        if "mx_get_positions" not in self.successful_query_tools:
            missing.append("当前持仓")
        if "mx_get_balance" not in self.successful_query_tools:
            missing.append("当前资金")
        if self.requires_orders and "mx_get_orders" not in self.successful_query_tools:
            missing.append("当前委托")
        return missing

    def is_ready(self) -> bool:
        return not self.missing_requirements()

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_type": self.run_type,
            "successful_query_tools": sorted(self.successful_query_tools),
            "failed_query_tools": sorted(self.failed_query_tools),
            "prefetched_tool_names": sorted(self.prefetched_tool_names),
            "prefetched_successful_query_tools": sorted(
                self.prefetched_successful_query_tools
            ),
            "prefetched_failed_query_tools": sorted(
                self.prefetched_failed_query_tools
            ),
            "mutations_executed": list(self.mutations_executed),
            "correction_attempts": self.correction_attempts,
            "captured_at": self.captured_at,
            "requires_orders": self.requires_orders,
            "ready": self.is_ready(),
            "missing_requirements": self.missing_requirements(),
        }


class _FinalEventBuffer:
    """Buffer model text for analysis/trade until the iteration is accepted.

    Final stream events are always held back.  While freshness is not ready,
    ``llm_message`` is buffered as well so text accompanying a query request
    cannot leak before the gate is satisfied.  ``flush`` emits buffered events;
    ``discard`` drops them.
    """

    _FINAL_EVENTS = frozenset({"final_started", "final_delta", "final_finished"})

    def __init__(
        self,
        inner_emit: Callable[..., Any],
        *,
        buffer_llm_messages: bool = False,
    ) -> None:
        self._inner = inner_emit
        self._buffer_llm_messages = buffer_llm_messages
        self._buffer: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event_type: str, **data: Any) -> None:
        should_buffer = event_type in self._FINAL_EVENTS or (
            self._buffer_llm_messages and event_type == "llm_message"
        )
        if should_buffer:
            self._buffer.append((event_type, data))
        else:
            self._inner(event_type, **data)

    def flush(self) -> None:
        for event_type, data in self._buffer:
            self._inner(event_type, **data)
        self._buffer.clear()

    def discard(self) -> None:
        self._buffer.clear()


class LLMMutationSafetyError(RuntimeError):
    """Raised when a mutation succeeded and the round must not be retried."""

    def __init__(
        self,
        message: str,
        *,
        mutations: list[str] | None = None,
        freshness: dict[str, Any] | None = None,
        prefetched_tool_calls: list[dict[str, Any]] | None = None,
        partial_result: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.mutations = list(mutations or [])
        self.freshness = dict(freshness or {})
        self.prefetched_tool_calls = list(prefetched_tool_calls or [])
        self.partial_result = partial_result


class LLMFreshnessError(RuntimeError):
    """Raised when current-run evidence cannot satisfy the freshness policy."""

    def __init__(
        self,
        message: str,
        *,
        prefetched_tool_calls: list[dict[str, Any]] | None = None,
        freshness: dict[str, Any] | None = None,
        missing_requirements: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.prefetched_tool_calls = list(prefetched_tool_calls or [])
        self.freshness = dict(freshness or {})
        self.missing_requirements = list(missing_requirements or [])


class LLMStreamCancelled(RuntimeError):
    """Raised when a streaming chat/run should stop because the client disconnected."""


class LLMEmptyResultError(Exception):
    """Raised when an agent loop completes but yields an empty answer.

    Used internally by ``_run_agent_loop_with_retry`` to signal that the whole
    attempt should be retried because the model returned empty content
    (covers chat/analysis/trade). It is raised and caught within
    ``llm_service`` only.
    """


class LLMUpstreamError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def normalize_reasoning_effort(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _raise_if_cancelled(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise LLMStreamCancelled("客户端连接已断开。")


def normalize_max_retries(value: Any) -> int:
    """Normalize configured extra retry count to 0..10 (default 3)."""
    if value is None:
        return _DEFAULT_LLM_MAX_RETRIES
    try:
        retries = int(value)
    except (TypeError, ValueError):
        return _DEFAULT_LLM_MAX_RETRIES
    return max(0, min(10, retries))


def _retry_delay_seconds(attempt_index: int) -> float:
    """Exponential backoff for retry attempt_index (0-based), capped at 20s."""
    raw = _LLM_RETRY_BASE_SECONDS * (2 ** max(0, int(attempt_index)))
    jittered = raw * (0.8 + 0.4 * random.random())
    return min(_LLM_RETRY_MAX_SECONDS, jittered)


def _sleep_with_cancel(
    delay_seconds: float,
    cancel_event: threading.Event | None = None,
) -> None:
    remaining = max(0.0, float(delay_seconds))
    while remaining > 0:
        _raise_if_cancelled(cancel_event)
        slice_seconds = min(_LLM_RETRY_CANCEL_POLL_SECONDS, remaining)
        time.sleep(slice_seconds)
        remaining -= slice_seconds
    _raise_if_cancelled(cancel_event)


def _annotate_retry_exhaustion(exc: BaseException, retries_attempted: int) -> BaseException:
    if retries_attempted <= 0:
        return exc
    suffix = f"，已重试 {retries_attempted} 次仍失败。"
    message = str(exc or "").rstrip()
    if "已重试" in message:
        return exc
    if message.endswith("。"):
        message = message[:-1]
    annotated = f"{message}{suffix}"
    if isinstance(exc, LLMUpstreamError):
        return LLMUpstreamError(annotated, status_code=exc.status_code)
    if isinstance(exc, RuntimeError) and not isinstance(exc, LLMStreamCancelled):
        return RuntimeError(annotated)
    return exc


def _format_error_message(prefix: str, detail: str) -> str:
    detail_text = str(detail or "").strip()
    if detail_text:
        return f"{prefix}: {detail_text}"
    return f"{prefix}。"


def _extract_error_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if value is None:
        return ""
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            part = _extract_error_text(item)
            if part:
                parts.append(part)
        return "; ".join(parts)
    if isinstance(value, dict):
        for key in ("message", "detail", "msg", "error_description", "reason"):
            part = _extract_error_text(value.get(key))
            if part:
                return part
        return _safe_json_dumps(value)
    return str(value).strip()


def _extract_error_detail(payload: Any) -> str:
    if isinstance(payload, dict):
        detail = _extract_error_text(payload.get("error"))
        if detail:
            return detail
        for key in ("message", "detail", "msg", "error_description"):
            detail = _extract_error_text(payload.get(key))
            if detail:
                return detail
    return _extract_error_text(payload)


def _decode_response_body(response: httpx.Response, raw_body: bytes) -> str:
    if not raw_body:
        return ""
    encoding = response.encoding or "utf-8"
    try:
        return raw_body.decode(encoding, errors="replace").strip()
    except LookupError:
        return raw_body.decode("utf-8", errors="replace").strip()


def _extract_response_error_detail(response: httpx.Response, raw_body: bytes) -> str:
    body_text = _decode_response_body(response, raw_body)
    if not body_text:
        return ""
    try:
        payload = json.loads(body_text)
    except json.JSONDecodeError:
        return body_text[:500]
    detail = _extract_error_detail(payload)
    return (detail or body_text)[:500]


def _raise_upstream_http_error(response: httpx.Response, raw_body: bytes) -> None:
    status = int(response.status_code)
    detail = _extract_response_error_detail(response, raw_body)
    if status == 401:
        raise LLMUpstreamError(
            _format_error_message("大模型 API Key 无效或已过期 (401)", detail),
            status_code=status,
        )
    if status == 400:
        raise LLMUpstreamError(
            _format_error_message("大模型请求参数错误 (400)", detail),
            status_code=status,
        )
    if status == 429:
        raise LLMUpstreamError(
            _format_error_message("大模型接口请求频率超限 (429)", detail),
            status_code=status,
        )
    raise LLMUpstreamError(
        _format_error_message(f"大模型接口返回错误 ({status})", detail),
        status_code=status,
    )


def _to_text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts).strip()
    return ""


def _safe_json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _slim_tool_result(tool_result: dict[str, Any]) -> dict[str, Any]:
    """Pass raw tool payloads to the model while keeping minimal metadata."""
    return {
        "ok": tool_result.get("ok"),
        "tool_name": tool_result.get("tool_name"),
        "summary": tool_result.get("summary"),
        "result": tool_result.get("result"),
    }


def _tool_spec_name(tool: Any) -> str:
    if not isinstance(tool, dict):
        return ""
    function_payload = tool.get("function")
    if not isinstance(function_payload, dict):
        return ""
    return str(function_payload.get("name") or "").strip()


def _normalize_tool_result(tool_name: str, value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {
        "ok": False,
        "tool_name": tool_name,
        "error": "工具返回了无法识别的结果格式。",
        "summary": "工具返回了无法识别的结果格式。",
        "result": None,
    }


def _truncate_text(text: str, limit: int) -> str:
    normalized = str(text or "")
    normalized_limit = max(0, int(limit))
    if len(normalized) <= normalized_limit:
        return normalized
    if normalized_limit == 0:
        return ""
    marker = "\n...[内容已截断]"
    if len(marker) >= normalized_limit:
        return marker[:normalized_limit]
    return normalized[: normalized_limit - len(marker)] + marker


def _json_for_snapshot(
    value: Any, limit: int = _PREFETCH_TOOL_RESULT_CHAR_LIMIT
) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        rendered = str(value)
    return _truncate_text(rendered, limit)


def _collect_disabled_skill_ids(app_settings: Any) -> set[str]:
    """从 app_settings 快照收集账户级禁用 Skill ID（全局层由 catalog 承担）。"""
    if app_settings is None:
        return set()
    raw = getattr(app_settings, "disabled_skill_ids", None)
    if raw is None:
        raw_json = getattr(app_settings, "disabled_skill_ids_json", None)
        if raw_json:
            try:
                parsed = json.loads(str(raw_json))
                if isinstance(parsed, list):
                    raw = parsed
            except (json.JSONDecodeError, TypeError):
                pass
    if raw is None:
        return set()
    if isinstance(raw, (set, frozenset)):
        return {str(item) for item in raw}
    if isinstance(raw, (list, tuple)):
        return {str(item).strip() for item in raw if str(item).strip()}
    return set()


def _requires_order_evidence(app_settings: Any, run_type: str) -> bool:
    if str(run_type or "").strip() != "trade":
        return False
    task_text = " ".join(
        [
            str(getattr(app_settings, "task_prompt", "") or ""),
            str(getattr(app_settings, "schedule_name", "") or ""),
        ]
    )
    return any(keyword in task_text for keyword in ("撤单", "委托", "订单"))


def _build_prefetch_snapshot(
    tool_calls: list[dict[str, Any]], captured_at: datetime
) -> str:
    shanghai_time = captured_at.astimezone(_SHANGHAI_TZ)
    header_lines = [
        "[本轮实时数据快照]",
        f"上海时间：{shanghai_time.isoformat(timespec='seconds')}",
        f"UTC 时间：{captured_at.astimezone(timezone.utc).isoformat(timespec='seconds')}",
        (
            "数据边界：历史对话中的行情、资讯、持仓、资金和委托均已过期；"
            "本轮只能依据此快照中的成功结果或随后本轮成功查询。"
        ),
    ]
    entries: list[tuple[list[str], Any | None]] = []
    for item in tool_calls:
        tool_name = str(item.get("name") or "").strip() or "unknown"
        tool_result = item.get("result")
        succeeded = FreshnessTracker._is_business_success(tool_result)
        entry_lines = [
            "",
            f"- {tool_name}：{'成功' if succeeded else '失败'}",
            f"  参数：{_json_for_snapshot(item.get('arguments') or {}, 600)}",
        ]
        raw_result: Any | None = None
        if succeeded and isinstance(tool_result, dict):
            entry_lines.append("  成功原始结果：")
            raw_result = tool_result.get("result")
        else:
            error_text = ""
            if isinstance(tool_result, dict):
                error_text = str(
                    tool_result.get("error")
                    or tool_result.get("summary")
                    or "业务结果未通过成功校验"
                ).strip()
            entry_lines.append(
                f"  失败原因：{_truncate_text(error_text or '工具调用失败', 600)}"
            )
        entries.append((entry_lines, raw_result))

    metadata_lines = list(header_lines)
    for entry_lines, _raw_result in entries:
        metadata_lines.extend(entry_lines)
    successful_count = sum(raw_result is not None for _lines, raw_result in entries)
    metadata_length = len("\n".join(metadata_lines)) + successful_count
    raw_result_limit = _PREFETCH_TOOL_RESULT_CHAR_LIMIT
    if successful_count:
        raw_result_limit = min(
            raw_result_limit,
            max(
                0,
                (_PREFETCH_SNAPSHOT_CHAR_LIMIT - metadata_length)
                // successful_count,
            ),
        )

    lines = list(header_lines)
    for entry_lines, raw_result in entries:
        lines.extend(entry_lines)
        if raw_result is not None:
            lines.append(_json_for_snapshot(raw_result, raw_result_limit))
    return _truncate_text("\n".join(lines), _PREFETCH_SNAPSHOT_CHAR_LIMIT)


def _freshness_correction_message(tracker: FreshnessTracker) -> str:
    missing = "、".join(tracker.missing_requirements()) or "本轮实时数据"
    return (
        "你刚才试图在本轮实时证据未齐备时直接给出最终答复，该文本已被系统屏蔽。"
        f"当前仍缺少：{missing}。请立即调用当前可见的查询工具补齐成功结果；"
        "不要引用历史行情或历史账户数字，也不要先输出结论。"
    )


def _blocked_mutation_result(tool_name: str, reason: str) -> dict[str, Any]:
    return {
        "ok": False,
        "tool_name": tool_name,
        "error": reason,
        "summary": reason,
        "result": None,
    }


def _iter_text_chunks(content: str, chunk_size: int = _FINAL_STREAM_CHUNK_SIZE):
    text = str(content or "")
    if not text:
        return

    for block in text.splitlines(keepends=True):
        if len(block) <= chunk_size:
            yield block
            continue

        start = 0
        while start < len(block):
            yield block[start : start + chunk_size]
            start += chunk_size


def _to_stream_text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def _merge_stream_tool_call(
    tool_calls: dict[int, dict[str, Any]],
    delta_payload: dict[str, Any],
) -> None:
    index = int(delta_payload.get("index") or 0)
    entry = tool_calls.setdefault(
        index,
        {
            "id": "",
            "type": "function",
            "function": {"name": "", "arguments": ""},
        },
    )

    call_id = delta_payload.get("id")
    if isinstance(call_id, str) and call_id:
        entry["id"] = call_id

    call_type = delta_payload.get("type")
    if isinstance(call_type, str) and call_type:
        entry["type"] = call_type

    function_payload = delta_payload.get("function")
    if not isinstance(function_payload, dict):
        return

    function_entry = entry.setdefault("function", {"name": "", "arguments": ""})
    name = function_payload.get("name")
    if isinstance(name, str) and name:
        function_entry["name"] += name

    arguments = function_payload.get("arguments")
    if isinstance(arguments, str) and arguments:
        function_entry["arguments"] += arguments


class LLMService:
    def _create_http_client(self, timeout_seconds: int) -> httpx.Client:
        return httpx.Client(timeout=float(timeout_seconds))

    def close(self) -> None:
        return None

    @staticmethod
    def _apply_reasoning_effort(
        payload: dict[str, Any], effort: str | None
    ) -> dict[str, Any]:
        text = normalize_reasoning_effort(effort)
        if text:
            payload["reasoning_effort"] = text
        else:
            payload.pop("reasoning_effort", None)
        return payload

    def chat(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        system_prompt: str | None,
        messages: list[dict[str, str]],
        timeout_seconds: int = 60,
        tool_context: dict[str, Any] | None = None,
        emit: Any = None,
        cancel_event: threading.Event | None = None,
        enable_reasoning_echo: bool = False,
        reasoning_effort: str | None = None,
    ) -> str:
        payload_messages: list[dict[str, Any]] = []
        chat_app_settings = (tool_context or {}).get("app_settings")
        disabled_skill_ids = _collect_disabled_skill_ids(chat_app_settings)
        effective_system_prompt = self._augment_system_prompt(
            system_prompt,
            run_type="chat",
            app_settings=chat_app_settings,
            disabled_skill_ids=disabled_skill_ids,
        )
        if effective_system_prompt:
            payload_messages.append(
                {"role": "system", "content": effective_system_prompt}
            )
        payload_messages.extend(messages)
        chat_tool_context = build_skill_context(
            run_type="chat",
            app_settings=chat_app_settings,
            client=(tool_context or {}).get("client"),
            base_context=tool_context,
        )

        def _chat_tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return skill_registry.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                context=chat_tool_context,
                disabled_skill_ids=disabled_skill_ids,
            )

        chat_reasoning_effort = normalize_reasoning_effort(
            reasoning_effort
            if reasoning_effort is not None
            else getattr(chat_app_settings, "llm_reasoning_effort", None)
        )
        result = self._run_agent_loop_with_retry(
            model=model,
            base_url=base_url,
            api_key=api_key,
            initial_messages=payload_messages,
            run_type="chat",
            timeout_seconds=timeout_seconds,
            tool_executor=_chat_tool_executor,
            emit=emit,
            cancel_event=cancel_event,
            enable_reasoning_echo=enable_reasoning_echo,
            reasoning_effort=chat_reasoning_effort,
            max_retries=normalize_max_retries(
                getattr(chat_app_settings, "llm_max_retries", _DEFAULT_LLM_MAX_RETRIES)
            ),
            disabled_skill_ids=disabled_skill_ids,
        )
        return str(result["final_answer"] or "").strip() or "模型本轮未返回可展示内容。"

    def build_initial_request_payload(self, app_settings: Any) -> dict[str, Any]:
        run_type = str(getattr(app_settings, "run_type", "analysis") or "analysis")
        disabled_skill_ids = _collect_disabled_skill_ids(app_settings)
        system_prompt = self._augment_system_prompt(
            app_settings.system_prompt,
            run_type=run_type,
            app_settings=app_settings,
            disabled_skill_ids=disabled_skill_ids,
        )
        payload = {
            "model": app_settings.llm_model,
            "temperature": _LLM_TEMPERATURE,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": getattr(app_settings, "task_prompt", "")},
            ],
            "tools": skill_registry.build_tools(
                run_type=run_type, disabled_skill_ids=disabled_skill_ids
            ),
            "tool_choice": "auto",
        }
        return self._apply_reasoning_effort(
            payload, getattr(app_settings, "llm_reasoning_effort", None)
        )

    def build_request_payload_from_messages(
        self,
        *,
        app_settings: Any,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        run_type = str(getattr(app_settings, "run_type", "analysis") or "analysis")
        disabled_skill_ids = _collect_disabled_skill_ids(app_settings)
        system_prompt = self._augment_system_prompt(
            app_settings.system_prompt,
            run_type=run_type,
            app_settings=app_settings,
            disabled_skill_ids=disabled_skill_ids,
        )
        payload_messages: list[dict[str, Any]] = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})
        payload_messages.extend(dict(message) for message in messages)
        payload = {
            "model": app_settings.llm_model,
            "temperature": _LLM_TEMPERATURE,
            "messages": payload_messages,
            "tools": skill_registry.build_tools(
                run_type=run_type, disabled_skill_ids=disabled_skill_ids
            ),
            "tool_choice": "auto",
        }
        return self._apply_reasoning_effort(
            payload, getattr(app_settings, "llm_reasoning_effort", None)
        )

    @staticmethod
    def _augment_system_prompt(
        base_prompt: str | None,
        *,
        run_type: str | None = None,
        app_settings: Any = None,
        disabled_skill_ids: set[str] | None = None,
    ) -> str:
        supplement = skill_registry.build_prompt_supplement(
            run_type=run_type, disabled_skill_ids=disabled_skill_ids
        )
        market_prompt = build_allowed_markets_prompt(
            get_allowed_markets_from_settings(app_settings)
        )
        seal_prompt = build_capital_seal_prompt(app_settings)
        prompt_parts = [
            str(base_prompt or "").strip(),
            str(supplement or "").strip(),
            str(market_prompt or "").strip(),
            str(seal_prompt or "").strip(),
        ]
        if str(run_type or "").strip() == "trade":
            prompt_parts.append(_TRADE_ENFORCEMENT_PROMPT)
        if str(run_type or "").strip() == "analysis":
            prompt_parts.append(_ANALYSIS_ENFORCEMENT_PROMPT)
        if str(run_type or "").strip() == "chat":
            prompt_parts.append(_CHAT_CONFIRMATION_APPEND_PROMPT)
        return "\n\n".join(part for part in prompt_parts if part)

    def run_agent(
        self,
        app_settings: Any,
        client: MXClient,
        emit: Any = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        return self.run_agent_with_messages(
            app_settings=app_settings,
            client=client,
            messages=[
                {
                    "role": "user",
                    "content": getattr(app_settings, "task_prompt", ""),
                }
            ],
            emit=emit,
        )

    def run_agent_with_messages(
        self,
        *,
        app_settings: Any,
        client: MXClient,
        messages: list[dict[str, Any]],
        emit: Any = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        if not app_settings.llm_base_url or not app_settings.llm_api_key:
            raise RuntimeError("未配置大模型接口，无法执行 AI 调度。")

        _emit = emit if callable(emit) else (lambda *_a, **_kw: None)
        run_type = str(getattr(app_settings, "run_type", "analysis") or "analysis")
        disabled_skill_ids = _collect_disabled_skill_ids(app_settings)
        run_tool_context = build_skill_context(
            run_type=run_type,
            app_settings=app_settings,
            client=client,
        )

        def _run_tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return skill_registry.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                context=run_tool_context,
                disabled_skill_ids=disabled_skill_ids,
            )

        agent_messages = [dict(message) for message in messages]
        prefetched_tool_calls: list[dict[str, Any]] = []
        freshness_tracker: FreshnessTracker | None = None
        enforce_freshness = run_type in {"analysis", "trade"}

        if enforce_freshness:
            freshness_tracker = FreshnessTracker(
                run_type=run_type,
                requires_orders=_requires_order_evidence(app_settings, run_type),
            )
            prefetch_plan: list[tuple[str, dict[str, Any]]] = [
                (
                    "mx_query_market",
                    {
                        "query": str(
                            getattr(app_settings, "market_query", "") or ""
                        ).strip()
                        or "上证指数今天走势和市场概况"
                    },
                ),
                (
                    "mx_search_news",
                    {
                        "query": str(
                            getattr(app_settings, "news_query", "") or ""
                        ).strip()
                        or "今天A股市场热点新闻"
                    },
                ),
                ("mx_get_positions", {}),
                ("mx_get_balance", {}),
            ]
            if freshness_tracker.requires_orders:
                prefetch_plan.append(("mx_get_orders", {}))

            _emit("stage", stage="prefetch", message="正在获取本轮实时数据快照")
            for index, (tool_name, arguments) in enumerate(prefetch_plan, start=1):
                tool_call_id = f"prefetch-{index}"
                _emit(
                    "tool_call",
                    phase="prefetch",
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    arguments=arguments,
                    status="running",
                )
                try:
                    tool_result = _normalize_tool_result(
                        tool_name, _run_tool_executor(tool_name, arguments)
                    )
                except Exception as exc:
                    tool_result = {
                        "ok": False,
                        "tool_name": tool_name,
                        "error": f"预取调用失败：{exc}",
                        "summary": f"预取调用失败：{exc}",
                        "result": None,
                    }
                captured_at = datetime.now(timezone.utc)
                prefetched_tool_calls.append(
                    {
                        "name": tool_name,
                        "arguments": dict(arguments),
                        "result": tool_result,
                        "prefetched": True,
                        "captured_at": captured_at.isoformat(),
                    }
                )
                freshness_tracker.record_prefetched_tool_result(
                    tool_name, tool_result
                )
                business_ok = FreshnessTracker._is_business_success(tool_result)
                _emit(
                    "tool_call",
                    phase="prefetch",
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    arguments=arguments,
                    status="done",
                    ok=business_ok,
                    summary=tool_result.get("summary")
                    or tool_result.get("error"),
                )

            snapshot_time = datetime.now(timezone.utc)
            freshness_tracker.captured_at = snapshot_time.isoformat()
            agent_messages.append(
                {
                    "role": "user",
                    "content": _build_prefetch_snapshot(
                        prefetched_tool_calls, snapshot_time
                    ),
                }
            )

        request_payload = self.build_request_payload_from_messages(
            app_settings=app_settings,
            messages=agent_messages,
        )
        try:
            result = self._run_agent_loop_with_retry(
                model=app_settings.llm_model,
                base_url=app_settings.llm_base_url,
                api_key=app_settings.llm_api_key,
                initial_messages=[dict(m) for m in request_payload["messages"]],
                run_type=run_type,
                timeout_seconds=getattr(app_settings, "timeout_seconds", 60),
                tool_executor=_run_tool_executor,
                emit=emit,
                enable_reasoning_echo=getattr(
                    app_settings, "llm_enable_reasoning_content_echo", False
                ),
                reasoning_effort=getattr(app_settings, "llm_reasoning_effort", None),
                max_retries=normalize_max_retries(
                    getattr(app_settings, "llm_max_retries", _DEFAULT_LLM_MAX_RETRIES)
                ),
                enforce_freshness=enforce_freshness,
                freshness_tracker=freshness_tracker,
                disabled_skill_ids=disabled_skill_ids,
            )
        except Exception as exc:
            freshness_audit = (
                freshness_tracker.to_dict() if freshness_tracker else {}
            )
            prefetched_audit = list(prefetched_tool_calls)
            if isinstance(exc, (LLMFreshnessError, LLMMutationSafetyError)):
                exc.prefetched_tool_calls = prefetched_audit
                exc.freshness = freshness_audit
                if isinstance(exc, LLMFreshnessError):
                    exc.missing_requirements = list(
                        freshness_audit.get("missing_requirements") or []
                    )
            else:
                try:
                    setattr(exc, "prefetched_tool_calls", prefetched_audit)
                    setattr(exc, "freshness", freshness_audit)
                except (AttributeError, TypeError):
                    pass
            raise

        freshness = freshness_tracker.to_dict() if freshness_tracker else None
        return (
            {
                "final_answer": result["final_answer"] or "模型本轮未返回可展示内容。",
                "tool_calls": result["tool_history"],
                "prefetched_tool_calls": prefetched_tool_calls,
                "freshness": freshness,
            },
            request_payload,
            {
                "responses": result["responses"],
                "final_message": result["final_message"],
            },
            {"messages": result["messages"], "freshness": freshness},
        )

    def _build_mutation_safety_result(
        self,
        *,
        error: BaseException | str,
        messages: list[dict[str, Any]],
        response_history: list[dict[str, Any]],
        tool_history: list[dict[str, Any]],
        tracker: FreshnessTracker,
        emit: Callable[..., Any],
    ) -> dict[str, Any]:
        mutation_names = "、".join(tracker.mutations_executed) or "变更工具"
        final_answer = (
            f"本轮已成功执行 {mutation_names}，但后续大模型响应异常。"
            "为避免重复下单或重复变更，系统未从初始消息重试；"
            "请以本轮工具执行记录和模拟账户实际状态为准。"
        )
        final_message = {"role": "assistant", "content": final_answer}
        if (
            messages
            and messages[-1].get("role") == "assistant"
            and not _to_text_content(messages[-1].get("content"))
            and not messages[-1].get("tool_calls")
        ):
            messages[-1] = final_message
        else:
            messages.append(final_message)
        self._emit_final_answer_stream(final_answer, emit=emit)
        return {
            "final_answer": final_answer,
            "raw_final_answer": final_answer,
            "tool_history": tool_history,
            "responses": response_history,
            "final_message": final_message,
            "messages": messages,
            "mutation_safety": {
                "error": str(error),
                "mutations": list(tracker.mutations_executed),
            },
        }

    def _agent_loop(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        initial_messages: list[dict[str, Any]],
        run_type: str,
        timeout_seconds: int,
        tool_executor: Callable[[str, dict[str, Any]], dict[str, Any]],
        emit: Any = None,
        cancel_event: threading.Event | None = None,
        enable_reasoning_echo: bool = False,
        reasoning_effort: str | None = None,
        max_retries: int | None = None,
        enforce_freshness: bool = False,
        freshness_tracker: FreshnessTracker | None = None,
        disabled_skill_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        _emit = emit if callable(emit) else (lambda *_a, **_kw: None)
        messages: list[dict[str, Any]] = [dict(m) for m in initial_messages]
        response_history: list[dict[str, Any]] = []
        tool_history: list[dict[str, Any]] = []
        normalized_effort = normalize_reasoning_effort(reasoning_effort)
        normalized_max_retries = normalize_max_retries(max_retries)
        normalized_run_type = str(run_type or "").strip()
        freshness_enabled = bool(
            enforce_freshness and normalized_run_type in {"analysis", "trade"}
        )
        tracker = freshness_tracker
        if freshness_enabled and tracker is None:
            tracker = FreshnessTracker(run_type=normalized_run_type)

        for iteration in range(_MAX_TOOL_ITERATIONS):
            _raise_if_cancelled(cancel_event)
            final_buffer = (
                _FinalEventBuffer(
                    _emit,
                    buffer_llm_messages=bool(
                        tracker is not None and not tracker.is_ready()
                    ),
                )
                if freshness_enabled
                else None
            )
            iteration_emit: Callable[..., Any] = final_buffer or _emit
            try:
                available_tools = skill_registry.build_tools(
                    run_type=run_type, disabled_skill_ids=disabled_skill_ids
                )
                query_only_phase = bool(
                    freshness_enabled and tracker is not None and not tracker.is_ready()
                )
                if query_only_phase:
                    available_tools = [
                        tool
                        for tool in available_tools
                        if _tool_spec_name(tool) in QUERY_TOOL_NAMES
                    ]
                    if not available_tools:
                        raise LLMFreshnessError(
                            "本轮实时证据未齐备，且当前运行没有可用的妙想查询工具。"
                        )

                iteration_payload = self._apply_reasoning_effort(
                    {
                        "model": model,
                        "temperature": _LLM_TEMPERATURE,
                        "messages": messages,
                        "tools": available_tools,
                        "tool_choice": "auto",
                    },
                    normalized_effort,
                )
                _emit(
                    "llm_request",
                    iteration=iteration + 1,
                    model=model,
                    freshness_ready=tracker.is_ready() if tracker else None,
                    query_only=query_only_phase,
                )
                response_payload = self._call_llm_stream(
                    base_url=base_url,
                    api_key=api_key,
                    payload=iteration_payload,
                    timeout_seconds=timeout_seconds,
                    emit=iteration_emit,
                    cancel_event=cancel_event,
                    max_retries=normalized_max_retries,
                )
                response_history.append(response_payload)

                choices = response_payload.get("choices") or []
                if not choices:
                    raise RuntimeError("大模型未返回 choices。")

                message = choices[0].get("message") or {}
                assistant_entry: dict[str, Any] = {
                    "role": "assistant",
                    "content": message.get("content") or "",
                }
                if message.get("tool_calls"):
                    assistant_entry["tool_calls"] = message["tool_calls"]
                if enable_reasoning_echo and message.get("reasoning_content"):
                    assistant_entry["reasoning_content"] = message["reasoning_content"]
                messages.append(assistant_entry)

                assistant_text = _to_text_content(message.get("content"))
                tool_calls = message.get("tool_calls") or []
                if assistant_text and tool_calls:
                    iteration_emit(
                        "llm_message", iteration=iteration + 1, content=assistant_text
                    )

                if not tool_calls:
                    if freshness_enabled and tracker is not None and not tracker.is_ready():
                        if final_buffer is not None:
                            final_buffer.discard()
                        if tracker.correction_attempts >= _MAX_FRESHNESS_CORRECTIONS:
                            missing = "、".join(tracker.missing_requirements())
                            raise LLMFreshnessError(
                                "大模型连续忽略本轮实时数据查询要求，"
                                f"仍缺少：{missing or '必要实时证据'}。"
                            )
                        tracker.correction_attempts += 1
                        correction = _freshness_correction_message(tracker)
                        messages.append({"role": "user", "content": correction})
                        _emit(
                            "freshness_correction",
                            attempt=tracker.correction_attempts,
                            max_attempts=_MAX_FRESHNESS_CORRECTIONS,
                            missing_requirements=tracker.missing_requirements(),
                            message=correction,
                        )
                        continue

                    final_message = assistant_text
                    if freshness_enabled and not final_message.strip():
                        if final_buffer is not None:
                            final_buffer.discard()
                        if tracker is not None and tracker.mutations_executed:
                            return self._build_mutation_safety_result(
                                error="大模型在成功变更后返回空答复",
                                messages=messages,
                                response_history=response_history,
                                tool_history=tool_history,
                                tracker=tracker,
                                emit=_emit,
                            )
                        raise LLMEmptyResultError("大模型返回空内容，视为无效。")

                    stream_meta = response_payload.get("stream_meta")
                    final_streamed = (
                        isinstance(stream_meta, dict)
                        and bool(stream_meta.get("final_streamed"))
                    )
                    if not final_streamed:
                        self._emit_final_answer_stream(
                            final_message, emit=iteration_emit
                        )
                    if final_buffer is not None:
                        final_buffer.flush()
                    return {
                        "final_answer": final_message,
                        "raw_final_answer": assistant_text,
                        "tool_history": tool_history,
                        "responses": response_history,
                        "final_message": message,
                        "messages": messages,
                    }

                if final_buffer is not None:
                    final_buffer.discard()

                for tool_call in tool_calls:
                    _raise_if_cancelled(cancel_event)
                    if not isinstance(tool_call, dict):
                        continue
                    function_payload = tool_call.get("function") or {}
                    tool_name = str(function_payload.get("name") or "").strip()
                    arguments_value = function_payload.get("arguments") or "{}"
                    if isinstance(arguments_value, dict):
                        arguments = dict(arguments_value)
                    else:
                        try:
                            arguments = json.loads(arguments_value)
                        except (json.JSONDecodeError, TypeError) as exc:
                            raise RuntimeError(
                                f"工具参数不是合法 JSON: {exc}"
                            ) from exc
                    if not isinstance(arguments, dict):
                        raise RuntimeError("工具参数必须是 JSON 对象。")

                    _emit(
                        "tool_call",
                        phase="llm",
                        tool_name=tool_name,
                        tool_call_id=tool_call.get("id"),
                        arguments=arguments,
                        status="running",
                    )

                    tool_result: dict[str, Any]
                    if (
                        freshness_enabled
                        and tracker is not None
                        and tool_name == "mx_moni_cancel"
                        and "mx_get_orders" not in tracker.successful_query_tools
                    ):
                        tracker.requires_orders = True
                        tool_result = _blocked_mutation_result(
                            tool_name,
                            "撤单前必须先成功查询本轮最新委托；请先调用 mx_get_orders。",
                        )
                    elif (
                        freshness_enabled
                        and tracker is not None
                        and tool_name in MUTATION_TOOL_NAMES
                        and (query_only_phase or not tracker.is_ready())
                    ):
                        tool_result = _blocked_mutation_result(
                            tool_name,
                            "本轮实时行情、持仓和资金证据尚未齐备，已拒绝变更操作。",
                        )
                    else:
                        tool_result = _normalize_tool_result(
                            tool_name, tool_executor(tool_name, arguments)
                        )

                    history_entry = {
                        "id": tool_call.get("id"),
                        "name": tool_name,
                        "arguments": arguments,
                        "result": tool_result,
                    }
                    tool_history.append(history_entry)
                    if freshness_enabled and tracker is not None:
                        tracker.record_tool_result(tool_name, tool_result)
                    _emit(
                        "tool_call",
                        phase="llm",
                        tool_name=tool_name,
                        tool_call_id=tool_call.get("id"),
                        arguments=arguments,
                        status="done",
                        ok=bool(tool_result.get("ok")),
                        summary=tool_result.get("summary")
                        or tool_result.get("error"),
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "content": _safe_json_dumps(
                                _slim_tool_result(tool_result)
                            ),
                        }
                    )
            except LLMStreamCancelled:
                if final_buffer is not None:
                    final_buffer.discard()
                raise
            except (LLMFreshnessError, LLMMutationSafetyError) as exc:
                if final_buffer is not None:
                    final_buffer.discard()
                if tracker is not None:
                    exc.freshness = tracker.to_dict()
                    if isinstance(exc, LLMFreshnessError):
                        exc.missing_requirements = tracker.missing_requirements()
                raise
            except Exception as exc:
                if final_buffer is not None:
                    final_buffer.discard()
                if freshness_enabled and tracker is not None and tracker.mutations_executed:
                    partial_result = self._build_mutation_safety_result(
                        error=exc,
                        messages=messages,
                        response_history=response_history,
                        tool_history=tool_history,
                        tracker=tracker,
                        emit=_emit,
                    )
                    raise LLMMutationSafetyError(
                        "成功变更后大模型响应异常，已禁止整轮重试。",
                        mutations=list(tracker.mutations_executed),
                        freshness=tracker.to_dict(),
                        partial_result=partial_result,
                    ) from exc
                raise

        if freshness_enabled and tracker is not None and tracker.mutations_executed:
            return self._build_mutation_safety_result(
                error="大模型工具调用轮次超限",
                messages=messages,
                response_history=response_history,
                tool_history=tool_history,
                tracker=tracker,
                emit=_emit,
            )
        raise RuntimeError("大模型工具调用轮次超限，已中止。")

    def _run_agent_loop_with_retry(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        initial_messages: list[dict[str, Any]],
        run_type: str,
        timeout_seconds: int,
        tool_executor: Callable[[str, dict[str, Any]], dict[str, Any]],
        emit: Any = None,
        cancel_event: threading.Event | None = None,
        enable_reasoning_echo: bool = False,
        reasoning_effort: str | None = None,
        max_retries: int | None = None,
        enforce_freshness: bool = False,
        freshness_tracker: FreshnessTracker | None = None,
        disabled_skill_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """Retry ordinary whole-round failures while preserving safety state.

        Provider failures and empty answers may replay ``initial_messages`` when
        no mutation has succeeded. Freshness-policy failures and mutation-safety
        failures never replay a round. Chat keeps the legacy retry behavior
        because its freshness gate is disabled.
        """
        _emit = emit if callable(emit) else (lambda *_a, **_kw: None)
        budget = normalize_max_retries(max_retries)
        last_error: BaseException | None = None
        normalized_effort = normalize_reasoning_effort(reasoning_effort)
        normalized_retries = normalize_max_retries(max_retries)
        normalized_run_type = str(run_type or "").strip()
        freshness_enabled = bool(
            enforce_freshness and normalized_run_type in {"analysis", "trade"}
        )
        tracker = freshness_tracker
        if freshness_enabled and tracker is None:
            tracker = FreshnessTracker(run_type=normalized_run_type)

        for attempt in range(budget + 1):
            _raise_if_cancelled(cancel_event)
            try:
                agent_loop_kwargs: dict[str, Any] = {
                    "model": model,
                    "base_url": base_url,
                    "api_key": api_key,
                    "initial_messages": initial_messages,
                    "run_type": run_type,
                    "timeout_seconds": timeout_seconds,
                    "tool_executor": tool_executor,
                    "emit": _emit,
                    "cancel_event": cancel_event,
                    "enable_reasoning_echo": enable_reasoning_echo,
                    "reasoning_effort": normalized_effort,
                    "max_retries": normalized_retries,
                }
                if freshness_enabled:
                    agent_loop_kwargs.update(
                        {
                            "enforce_freshness": True,
                            "freshness_tracker": tracker,
                        }
                    )
                if disabled_skill_ids:
                    agent_loop_kwargs["disabled_skill_ids"] = disabled_skill_ids
                result = self._agent_loop(**agent_loop_kwargs)
                raw_answer = str(result.get("raw_final_answer") or "").strip()
                if not raw_answer:
                    if tracker is not None and tracker.mutations_executed:
                        return self._build_mutation_safety_result(
                            error="大模型在成功变更后返回空答复",
                            messages=result.get("messages")
                            if isinstance(result.get("messages"), list)
                            else [],
                            response_history=result.get("responses")
                            if isinstance(result.get("responses"), list)
                            else [],
                            tool_history=result.get("tool_history")
                            if isinstance(result.get("tool_history"), list)
                            else [],
                            tracker=tracker,
                            emit=_emit,
                        )
                    raise LLMEmptyResultError("大模型返回空内容，视为无效。")
                return result
            except LLMStreamCancelled:
                raise
            except LLMFreshnessError as exc:
                if tracker is not None:
                    exc.freshness = tracker.to_dict()
                    exc.missing_requirements = tracker.missing_requirements()
                raise
            except LLMMutationSafetyError as exc:
                if tracker is not None:
                    exc.freshness = tracker.to_dict()
                if exc.partial_result is not None:
                    return exc.partial_result
                raise
            except Exception as exc:
                if tracker is not None and tracker.mutations_executed:
                    raise LLMMutationSafetyError(
                        "成功变更后发生异常，已禁止从初始消息整轮重试。",
                        mutations=list(tracker.mutations_executed),
                        freshness=tracker.to_dict(),
                    ) from exc

                last_error = exc
                if attempt >= budget:
                    raise _annotate_retry_exhaustion(exc, attempt) from exc

                if tracker is not None:
                    tracker.reset_for_round_retry()
                delay = _retry_delay_seconds(attempt)
                logger.warning(
                    "LLM agent round attempt %s/%s failed (%s); retrying in %.2fs",
                    attempt + 1,
                    budget + 1,
                    exc,
                    delay,
                )
                _emit(
                    "llm_retry",
                    attempt=attempt + 1,
                    max_retries=budget,
                    delay_seconds=round(delay, 3),
                    message=(
                        f"大模型本轮无效，{delay:.1f} 秒后重试 "
                        f"({attempt + 1}/{budget})…"
                    ),
                    error=str(exc),
                )
                _sleep_with_cancel(delay, cancel_event)

        if last_error is not None:
            raise _annotate_retry_exhaustion(last_error, budget)
        raise RuntimeError("大模型整轮请求失败。")

    def _emit_final_answer_stream(self, content: str, *, emit: Callable[..., Any]) -> None:
        final_text = str(content or "").strip()
        emit("final_started", char_count=len(final_text))

        streamed = 0
        for chunk in _iter_text_chunks(final_text):
            streamed += len(chunk)
            emit(
                "final_delta",
                delta=chunk,
                streamed_chars=streamed,
            )

        emit("final_finished", content=final_text, char_count=len(final_text))

    def _call_llm_stream(
        self,
        *,
        base_url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout_seconds: int,
        emit: Callable[..., Any] | None = None,
        cancel_event: threading.Event | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        """Single logical stream attempt (no transient retry).

        Whole-round retry is handled by ``_run_agent_loop_with_retry``; this
        method performs one HTTP attempt and keeps the include_usage 400
        compatibility fallback (which does not consume the retry budget).
        ``max_retries`` is retained for signature compatibility (some callers /
        tests still pass it) but no longer drives retries here.
        """
        _emit = emit if callable(emit) else (lambda *_a, **_kw: None)
        _raise_if_cancelled(cancel_event)
        try:
            return self._call_llm_stream_once(
                base_url=base_url,
                api_key=api_key,
                payload=payload,
                timeout_seconds=timeout_seconds,
                emit=_emit,
                cancel_event=cancel_event,
            )
        except LLMStreamCancelled:
            raise
        except Exception as exc:
            raise _annotate_retry_exhaustion(exc, 0) from exc

    def _call_llm_stream_once(
        self,
        *,
        base_url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout_seconds: int,
        emit: Callable[..., Any] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Single logical stream attempt, including include_usage 400 fallback."""
        stream_payload = dict(payload)
        stream_payload["stream"] = True

        last_error: LLMUpstreamError | None = None
        for include_usage in (True, False):
            _raise_if_cancelled(cancel_event)
            attempt_payload = dict(stream_payload)
            if include_usage:
                attempt_payload["stream_options"] = {"include_usage": True}
            try:
                return self._consume_llm_stream(
                    base_url=base_url,
                    api_key=api_key,
                    payload=attempt_payload,
                    timeout_seconds=timeout_seconds,
                    emit=emit,
                    cancel_event=cancel_event,
                )
            except LLMUpstreamError as exc:
                last_error = exc
                error_text = str(exc).casefold()
                is_reasoning_replay_error = "reasoning_content" in error_text
                if (
                    not include_usage
                    or exc.status_code != 400
                    or is_reasoning_replay_error
                ):
                    raise

        if last_error is not None:
            raise last_error
        raise RuntimeError("大模型流式请求失败。")

    def _consume_llm_stream(
        self,
        *,
        base_url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout_seconds: int,
        emit: Callable[..., Any] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        _emit = emit if callable(emit) else (lambda *_a, **_kw: None)
        _raise_if_cancelled(cancel_event)

        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            with self._create_http_client(timeout_seconds) as http_client:
                with http_client.stream(
                    "POST",
                    url,
                    headers=headers,
                    json=payload,
                ) as response:
                    if response.is_error:
                        _raise_upstream_http_error(response, response.read())
                    return self._parse_llm_stream_response(
                        lines=response.iter_lines(),
                        emit=_emit,
                        cancel_event=cancel_event,
                    )
        except LLMUpstreamError:
            raise
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"大模型接口请求超时 ({timeout_seconds}s)，请检查网络或增加超时时间。"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"大模型接口请求失败: {exc}") from exc

    def _parse_llm_stream_response(
        self,
        *,
        lines: Iterable[str],
        emit: Callable[..., Any],
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        data_lines: list[str] = []
        content_parts: list[str] = []
        reasoning_content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, Any] | None = None
        finish_reason: str | None = None
        response_id: str | None = None
        response_model: str | None = None
        response_created: Any = None
        response_object: str | None = None
        chunk_count = 0
        stream_mode: str | None = None
        final_started = False

        def _flush_payload(raw_payload: str) -> None:
            nonlocal usage
            nonlocal finish_reason
            nonlocal response_id
            nonlocal response_model
            nonlocal response_created
            nonlocal response_object
            nonlocal chunk_count
            nonlocal stream_mode
            nonlocal final_started

            if not raw_payload:
                return
            if raw_payload == "[DONE]":
                return

            chunk = json.loads(raw_payload)
            chunk_count += 1

            if "error" in chunk:
                raise LLMUpstreamError(
                    _format_error_message(
                        "大模型流式响应错误",
                        _extract_error_detail(chunk),
                    )
                )

            if any(key in chunk for key in ("message", "detail")) and not chunk.get(
                "choices"
            ):
                raise LLMUpstreamError(
                    _format_error_message(
                        "大模型流式响应错误",
                        _extract_error_detail(chunk),
                    )
                )

            if isinstance(chunk.get("usage"), dict):
                usage = chunk["usage"]

            if response_id is None and isinstance(chunk.get("id"), str):
                response_id = chunk["id"]
            if response_model is None and isinstance(chunk.get("model"), str):
                response_model = chunk["model"]
            if response_object is None and isinstance(chunk.get("object"), str):
                response_object = chunk["object"]
            if response_created is None and chunk.get("created") is not None:
                response_created = chunk.get("created")

            choices = chunk.get("choices")
            if not isinstance(choices, list) or not choices:
                return

            choice = choices[0] if isinstance(choices[0], dict) else {}
            finish_value = choice.get("finish_reason")
            if isinstance(finish_value, str) and finish_value:
                finish_reason = finish_value

            delta = choice.get("delta")
            if not isinstance(delta, dict):
                return

            delta_tool_calls = delta.get("tool_calls")
            if isinstance(delta_tool_calls, list) and delta_tool_calls:
                if stream_mode is None:
                    stream_mode = "tool"
                for item in delta_tool_calls:
                    if isinstance(item, dict):
                        _merge_stream_tool_call(tool_calls, item)

            delta_text = _to_stream_text_content(delta.get("content"))
            if delta_text:
                content_parts.append(delta_text)
                if stream_mode is None:
                    stream_mode = "final"
                if stream_mode == "final":
                    if not final_started:
                        emit("final_started")
                        final_started = True
                    emit("final_delta", delta=delta_text)

            delta_reasoning = _to_stream_text_content(delta.get("reasoning_content"))
            if delta_reasoning:
                reasoning_content_parts.append(delta_reasoning)

        for raw_line in lines:
            _raise_if_cancelled(cancel_event)
            line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8")
            line = line.rstrip("\r\n")
            if not line:
                payload = "\n".join(data_lines)
                data_lines.clear()
                _flush_payload(payload)
                if payload == "[DONE]":
                    break
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())

        if data_lines:
            _flush_payload("\n".join(data_lines))

        final_text = "".join(content_parts)
        final_reasoning_text = "".join(reasoning_content_parts)
        if stream_mode != "tool":
            if not final_started:
                emit("final_started", char_count=len(final_text))
                final_started = True
            emit("final_finished", content=final_text, char_count=len(final_text))

        message: dict[str, Any] = {
            "role": "assistant",
            "content": final_text,
        }
        if final_reasoning_text:
            message["reasoning_content"] = final_reasoning_text
        ordered_tool_calls = [tool_calls[idx] for idx in sorted(tool_calls)]
        if ordered_tool_calls:
            message["tool_calls"] = ordered_tool_calls

        response_payload: dict[str, Any] = {
            "id": response_id,
            "object": response_object or "chat.completion",
            "created": response_created,
            "model": response_model,
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": finish_reason,
                }
            ],
            "stream_meta": {
                "chunk_count": chunk_count,
                "final_streamed": stream_mode != "tool",
            },
        }
        if usage is not None:
            response_payload["usage"] = usage
        return response_payload

    def _call_llm(
        self,
        *,
        base_url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout_seconds: int,
        max_retries: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """Non-streaming LLM call (single attempt, no transient retry).

        Kept for parity; primary runtime path uses ``_call_llm_stream``.
        Whole-round retry is handled by ``_run_agent_loop_with_retry``.
        """
        _raise_if_cancelled(cancel_event)
        try:
            return self._call_llm_once(
                base_url=base_url,
                api_key=api_key,
                payload=payload,
                timeout_seconds=timeout_seconds,
            )
        except LLMStreamCancelled:
            raise
        except Exception as exc:
            raise _annotate_retry_exhaustion(exc, 0) from exc

    def _call_llm_once(
        self,
        *,
        base_url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        try:
            with self._create_http_client(timeout_seconds) as http_client:
                response = http_client.post(url, headers=headers, json=payload)
                if response.is_error:
                    _raise_upstream_http_error(response, response.content)
                response.raise_for_status()
        except LLMUpstreamError:
            raise
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"大模型接口请求超时 ({timeout_seconds}s)，请检查网络或增加超时时间。"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(f"大模型接口请求失败: {exc}") from exc
        return response.json()

    def run_structured_json_call(
        self,
        *,
        model: str,
        base_url: str,
        api_key: str,
        system_prompt: str | None,
        user_prompt: str,
        timeout_seconds: int = 90,
        reasoning_effort: str | None = None,
        max_retries: int | None = None,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, Any]:
        """UZI 受限结构化 JSON 分析入口（文档 §7.1 / §13.1 / §13.4）。

        - 强制 ``response_format={"type": "json_object"}``，要求模型输出 JSON。
        - 复用 ``_call_llm_stream`` 的流式解析与 include_usage 400 兼容逻辑，
          并按 ``max_retries``（来自 AppSettings）做指数退避重试。
        - 不注入交易 freshness 逻辑，不注入交易/分析/聊天执行提示词——
          UZI 使用独立、版本化的研究提示词（由调用方提供）。
        - 不记录 API Key，不记录隐藏推理（§16.3）。

        返回 ``{"content": <json 文本>, "payload": <原始响应>}``。
        """
        budget = normalize_max_retries(max_retries)
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_prompt})

        last_error: BaseException | None = None
        for attempt in range(budget + 1):
            _raise_if_cancelled(cancel_event)
            payload = self._apply_reasoning_effort(
                {
                    "model": model,
                    "temperature": _LLM_TEMPERATURE,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                },
                reasoning_effort,
            )
            try:
                response_payload = self._call_llm_stream(
                    base_url=base_url,
                    api_key=api_key,
                    payload=payload,
                    timeout_seconds=timeout_seconds,
                    cancel_event=cancel_event,
                )
            except LLMStreamCancelled:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= budget:
                    raise _annotate_retry_exhaustion(exc, attempt) from exc
                delay = _retry_delay_seconds(attempt)
                logger.warning(
                    "UZI structured JSON call attempt %s/%s failed (%s); retrying in %.2fs",
                    attempt + 1,
                    budget + 1,
                    exc,
                    delay,
                )
                _sleep_with_cancel(delay, cancel_event)
                continue

            choices = response_payload.get("choices") or []
            if not choices:
                raise RuntimeError("大模型未返回 choices。")
            message = choices[0].get("message") or {}
            content = _to_text_content(message.get("content"))
            if not content.strip():
                raise RuntimeError("大模型返回空内容，视为无效。")
            return {
                "content": content,
                "payload": response_payload,
            }

        if last_error is not None:
            raise _annotate_retry_exhaustion(last_error, budget)
        raise RuntimeError("大模型结构化调用失败。")


llm_service = LLMService()
