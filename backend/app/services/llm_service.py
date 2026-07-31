from __future__ import annotations

import json
import logging
import random
import threading
import time
from typing import Any, Callable, Iterable

import httpx

from app.skills.providers import build_skill_context
from skills.mx_core.client import MXClient
from skills.mx_core.capital_seal import build_capital_seal_prompt
from skills.mx_core.markets import (
    build_allowed_markets_prompt,
    get_allowed_markets_from_settings,
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
    "mx_search_news / mx_get_positions / mx_get_balance 等查询类工具确认当前行情、资讯、"
    "持仓与资金状态，再给出结论。严禁在零工具调用的前提下直接输出任何分析或交易结论。\n"
    "5. 在文本中说「建议买入」「应该卖出」「可以建仓」等不会触发任何实际操作"
    "——只有工具调用才会执行交易。如果你不调用函数，交易就不会发生。\n"
    "6. 第一轮被强制要求调用工具时，应优先选择查询类工具；不得在未获取最新行情、持仓、资金数据前"
    "直接调用 mx_moni_trade / mx_moni_cancel。"
)
_ANALYSIS_ENFORCEMENT_PROMPT = (
    "## 数据获取强制规则（analysis 模式专属）\n"
    "你当前处于分析（analysis）模式，你的分析结论必须建立在真实数据之上。\n"
    "关键规则：\n"
    "1. 必须先调用查询类工具，获取最新行情、资讯、持仓与资金数据，再给出分析结论。\n"
    "2. 严禁在零工具调用的前提下直接输出分析结论。\n"
    "3. 第一轮被强制要求调用工具时，应优先选择上述查询类工具。"
)


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
        effective_system_prompt = self._augment_system_prompt(
            system_prompt,
            run_type="chat",
            app_settings=chat_app_settings,
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
        )
        return str(result["final_answer"] or "").strip() or "模型本轮未返回可展示内容。"

    def build_initial_request_payload(self, app_settings: Any) -> dict[str, Any]:
        run_type = str(getattr(app_settings, "run_type", "analysis") or "analysis")
        system_prompt = self._augment_system_prompt(
            app_settings.system_prompt,
            run_type=run_type,
            app_settings=app_settings,
        )
        payload = {
            "model": app_settings.llm_model,
            "temperature": _LLM_TEMPERATURE,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": getattr(app_settings, "task_prompt", "")},
            ],
            "tools": skill_registry.build_tools(run_type=run_type),
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
        system_prompt = self._augment_system_prompt(
            app_settings.system_prompt,
            run_type=run_type,
            app_settings=app_settings,
        )
        payload_messages: list[dict[str, Any]] = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})
        payload_messages.extend(dict(message) for message in messages)
        payload = {
            "model": app_settings.llm_model,
            "temperature": _LLM_TEMPERATURE,
            "messages": payload_messages,
            "tools": skill_registry.build_tools(run_type=run_type),
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
    ) -> str:
        supplement = skill_registry.build_prompt_supplement(run_type=run_type)
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
        request_payload = self.build_request_payload_from_messages(
            app_settings=app_settings,
            messages=messages,
        )
        if not app_settings.llm_base_url or not app_settings.llm_api_key:
            raise RuntimeError("未配置大模型接口，无法执行 AI 调度。")

        run_type = str(getattr(app_settings, "run_type", "analysis") or "analysis")

        def _run_tool_executor(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
            return skill_registry.execute_tool(
                tool_name=tool_name,
                arguments=arguments,
                context=build_skill_context(
                    run_type=run_type,
                    app_settings=app_settings,
                    client=client,
                ),
            )

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
        )

        return (
            {
                "final_answer": result["final_answer"] or "模型本轮未返回可展示内容。",
                "tool_calls": result["tool_history"],
            },
            request_payload,
            {
                "responses": result["responses"],
                "final_message": result["final_message"],
            },
            {"messages": result["messages"]},
        )

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
    ) -> dict[str, Any]:
        _emit = emit if callable(emit) else (lambda *_a, **_kw: None)
        messages: list[dict[str, Any]] = [dict(m) for m in initial_messages]
        response_history: list[dict[str, Any]] = []
        tool_history: list[dict[str, Any]] = []
        normalized_effort = normalize_reasoning_effort(reasoning_effort)
        normalized_max_retries = normalize_max_retries(max_retries)

        for iteration in range(_MAX_TOOL_ITERATIONS):
            _raise_if_cancelled(cancel_event)
            forced_first_round = iteration == 0 and str(run_type or "").strip() in {
                "analysis",
                "trade",
            }
            iteration_payload = self._apply_reasoning_effort(
                {
                    "model": model,
                    "temperature": _LLM_TEMPERATURE,
                    "messages": messages,
                    "tools": skill_registry.build_tools(run_type=run_type),
                    "tool_choice": "required" if forced_first_round else "auto",
                },
                normalized_effort,
            )
            _emit("llm_request", iteration=iteration + 1, model=model)
            response_payload = self._call_llm_stream(
                base_url=base_url,
                api_key=api_key,
                payload=iteration_payload,
                timeout_seconds=timeout_seconds,
                emit=_emit,
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
                _emit("llm_message", iteration=iteration + 1, content=assistant_text)

            if not tool_calls:
                final_message = assistant_text
                stream_meta = response_payload.get("stream_meta")
                final_streamed = (
                    isinstance(stream_meta, dict)
                    and bool(stream_meta.get("final_streamed"))
                )
                if not final_streamed:
                    self._emit_final_answer_stream(final_message, emit=_emit)
                return {
                    "final_answer": final_message,
                    "raw_final_answer": assistant_text,
                    "tool_history": tool_history,
                    "responses": response_history,
                    "final_message": message,
                    "messages": messages,
                }

            for tool_call in tool_calls:
                _raise_if_cancelled(cancel_event)
                if not isinstance(tool_call, dict):
                    continue
                function_payload = tool_call.get("function") or {}
                tool_name = str(function_payload.get("name") or "").strip()
                arguments_text = function_payload.get("arguments") or "{}"
                try:
                    arguments = json.loads(arguments_text)
                except json.JSONDecodeError as exc:
                    raise RuntimeError(f"工具参数不是合法 JSON: {exc}") from exc

                _emit(
                    "tool_call",
                    phase="llm",
                    tool_name=tool_name,
                    tool_call_id=tool_call.get("id"),
                    arguments=arguments,
                    status="running",
                )
                tool_result = tool_executor(tool_name, arguments)
                _emit(
                    "tool_call",
                    phase="llm",
                    tool_name=tool_name,
                    tool_call_id=tool_call.get("id"),
                    arguments=arguments,
                    status="done",
                    ok=bool(tool_result.get("ok")),
                    summary=tool_result.get("summary"),
                )
                tool_history.append(
                    {
                        "id": tool_call.get("id"),
                        "name": tool_name,
                        "arguments": arguments,
                        "result": tool_result,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "content": _safe_json_dumps(_slim_tool_result(tool_result)),
                    }
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
    ) -> dict[str, Any]:
        """Run ``_agent_loop`` with a unified whole-round retry policy.

        Any exception raised by ``_agent_loop`` triggers a retry (up to
        ``max_retries`` extra attempts). A normally-returned result is also
        rejected (and retried) when it is deemed invalid for the run type:

        * analysis/trade: the round must have invoked at least one tool;
          otherwise (including empty content) it is retried.
        * chat: only a truly empty answer (raw text blank) is retried; a plain
          text answer without tool calls is a valid success.

        ``LLMStreamCancelled`` (client disconnect) is never retried.
        """
        _emit = emit if callable(emit) else (lambda *_a, **_kw: None)
        budget = normalize_max_retries(max_retries)
        last_error: BaseException | None = None
        normalized_effort = normalize_reasoning_effort(reasoning_effort)
        normalized_retries = normalize_max_retries(max_retries)

        for attempt in range(budget + 1):
            _raise_if_cancelled(cancel_event)
            try:
                result = self._agent_loop(
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    initial_messages=initial_messages,
                    run_type=run_type,
                    timeout_seconds=timeout_seconds,
                    tool_executor=tool_executor,
                    emit=_emit,
                    cancel_event=cancel_event,
                    enable_reasoning_echo=enable_reasoning_echo,
                    reasoning_effort=normalized_effort,
                    max_retries=normalized_retries,
                )
                raw_answer = str(result.get("raw_final_answer") or "").strip()
                if not raw_answer:
                    raise LLMEmptyResultError("大模型返回空内容，视为无效。")
                return result
            except LLMStreamCancelled:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= budget:
                    raise _annotate_retry_exhaustion(exc, attempt) from exc

                delay = _retry_delay_seconds(attempt)
                if str(run_type or "").strip() == "trade":
                    logger.warning(
                        "trade 模式整轮重试，可能重复下单 "
                        "(attempt %s/%s, %s)",
                        attempt + 1,
                        budget,
                        exc,
                    )
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
                if not include_usage or exc.status_code != 400:
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


llm_service = LLMService()
