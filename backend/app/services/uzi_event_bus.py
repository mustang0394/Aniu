"""UZI 报告任务事件总线（文档 §10.5）。

按 ``report_id`` 订阅；每个任务有零或多个订阅者，发布的事件广播到所有
存活订阅队列。事件类型固定为：``snapshot / status_changed / progress /
completed / failed / cancelled / heartbeat``。

客户端断线重连后先发数据库快照，再订阅内存事件；任务已终态时发送
快照与终态事件后关闭。进度消息只描述阶段、数据源、完成数量和错误摘要，
绝不推送模型隐藏推理过程（§6）。
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_MAX_SUBSCRIBER_BACKLOG = 500
_REPLAY_TTL_SECONDS = 120.0
_MAX_REPLAY_EVENTS = 500

_TERMINAL_EVENT_TYPES = frozenset({"completed", "failed", "cancelled"})


class _UziChannel:
    __slots__ = ("report_id", "subscribers", "history", "finished_at", "lock")

    def __init__(self, report_id: int) -> None:
        self.report_id = report_id
        self.subscribers: list[queue.Queue[dict[str, Any] | None]] = []
        self.history: list[dict[str, Any]] = []
        self.finished_at: float | None = None
        self.lock = threading.Lock()


class UziEventBus:
    def __init__(self) -> None:
        self._channels: dict[int, _UziChannel] = {}
        self._global_lock = threading.Lock()

    def _get_or_create(self, report_id: int) -> _UziChannel:
        with self._global_lock:
            channel = self._channels.get(report_id)
            if channel is None:
                channel = _UziChannel(report_id)
                self._channels[report_id] = channel
            return channel

    def _get(self, report_id: int) -> _UziChannel | None:
        with self._global_lock:
            return self._channels.get(report_id)

    def publish(
        self,
        report_id: int,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "type": event_type,
            "report_id": report_id,
            "ts": time.time(),
        }
        if data:
            payload.update(data)

        channel = self._get_or_create(report_id)
        with channel.lock:
            channel.history.append(payload)
            if len(channel.history) > _MAX_REPLAY_EVENTS:
                del channel.history[: len(channel.history) - _MAX_REPLAY_EVENTS]
            subscribers = list(channel.subscribers)

        for sub in subscribers:
            try:
                if sub.qsize() >= _MAX_SUBSCRIBER_BACKLOG:
                    logger.warning(
                        "uzi_event_bus subscriber backlog exceeded for report_id=%s, dropping",
                        report_id,
                    )
                    continue
                sub.put_nowait(payload)
            except Exception:  # pragma: no cover - defensive
                logger.exception("uzi_event_bus publish to subscriber failed")

        if event_type in _TERMINAL_EVENT_TYPES:
            with channel.lock:
                channel.finished_at = time.time()
            self._maybe_expire_finished()

    def subscribe(
        self, report_id: int
    ) -> tuple[queue.Queue[dict[str, Any] | None], list[dict[str, Any]]]:
        channel = self._get_or_create(report_id)
        sub: queue.Queue[dict[str, Any] | None] = queue.Queue()
        with channel.lock:
            snapshot = list(channel.history)
            channel.subscribers.append(sub)
        return sub, snapshot

    def unsubscribe(
        self, report_id: int, sub: queue.Queue[dict[str, Any] | None]
    ) -> None:
        channel = self._get(report_id)
        if channel is None:
            return
        with channel.lock:
            try:
                channel.subscribers.remove(sub)
            except ValueError:
                pass

    def is_finished(self, report_id: int) -> bool:
        channel = self._get(report_id)
        if channel is None:
            return False
        with channel.lock:
            return channel.finished_at is not None

    def _maybe_expire_finished(self) -> None:
        now = time.time()
        with self._global_lock:
            expired = [
                rid
                for rid, ch in self._channels.items()
                if ch.finished_at is not None
                and now - ch.finished_at > _REPLAY_TTL_SECONDS
                and not ch.subscribers
            ]
            for rid in expired:
                self._channels.pop(rid, None)

    def stream(
        self,
        report_id: int,
        *,
        stop_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        """阻塞生成器：先回放内存快照，再实时订阅直到终态事件或停止。"""
        sub, snapshot = self.subscribe(report_id)
        try:
            for event in snapshot:
                yield event
            if snapshot and snapshot[-1].get("type") in _TERMINAL_EVENT_TYPES:
                return
            while True:
                if stop_event is not None and stop_event.is_set():
                    return
                try:
                    event = sub.get(timeout=15.0)
                except queue.Empty:
                    yield {
                        "type": "heartbeat",
                        "report_id": report_id,
                        "ts": time.time(),
                    }
                    continue
                if event is None:
                    return
                yield event
                if event.get("type") in _TERMINAL_EVENT_TYPES:
                    return
        finally:
            self.unsubscribe(report_id, sub)


uzi_event_bus = UziEventBus()
