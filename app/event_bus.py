"""事件总线 - 异步Pub/Sub，支持WebSocket实时推送"""
from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from app.models import Event
from app.observability import current_trace_id

logger = logging.getLogger(__name__)

# 单个订阅者的队列容量，超过后新事件将被丢弃
SUBSCRIBER_QUEUE_MAXSIZE = 1000


class EventBus:
    """异步事件总线，支持订阅/发布模式"""

    def __init__(self):
        # channel -> list of subscriber queues
        self._subscribers: dict[str, list[asyncio.Queue[Event]]] = defaultdict(list)
        # event_type -> list of callback handlers
        self._handlers: dict[str, list[Callable[[Event], Awaitable[None]]]] = defaultdict(list)
        self._lock = asyncio.Lock()
        # 丢弃事件计数，便于监控订阅者消费能力
        self.dropped_events = 0

    async def publish(self, event_type: str, data: dict[str, Any] | None = None) -> Event:
        """发布事件到事件总线"""
        event = Event(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            data=data or {},
            timestamp=datetime.now(timezone.utc),
            trace_id=current_trace_id(),
        )

        # Notify event type handlers
        handlers = self._handlers.get(event_type, [])
        for handler in handlers:
            try:
                await handler(event)
            except Exception as e:
                # 处理器异常不应影响事件总线
                logger.warning("Handler error for %s: %s", event_type, e, exc_info=True)

        # Publish to channels (based on event type mapping)
        channels = self._event_to_channels(event_type, data or {})
        async with self._lock:
            for channel in channels:
                queues = self._subscribers.get(channel, [])
                for q in queues:
                    try:
                        q.put_nowait(event)
                    except asyncio.QueueFull:
                        # 队列满说明订阅者消费不过来，丢弃并记录，避免阻塞发布方
                        self.dropped_events += 1
                        logger.warning(
                            "EventBus queue full, dropping event %s on channel '%s' "
                            "(total dropped: %d)",
                            event_type, channel, self.dropped_events,
                        )

        return event

    def _event_to_channels(self, event_type: str, data: dict[str, Any]) -> list[str]:
        """将事件类型映射到订阅频道"""
        channels = {event_type}  # 始终推送到精确匹配的频道

        # 智能体专属频道
        if "agent_id" in data:
            channels.add(f"agent:{data['agent_id']}")
        if "assignee_id" in data:
            channels.add(f"agent:{data['assignee_id']}")
        if "author_id" in data:
            channels.add(f"agent:{data['author_id']}")

        # 任务频道
        if event_type.startswith("task."):
            channels.add("tasks")

        # 黑板条目频道
        if event_type.startswith("entry."):
            topic = data.get("topic")
            if topic:
                channels.add(f"entries:{topic}")
            channels.add("entries")

        # 冲突频道
        if event_type.startswith("conflict."):
            channels.add("conflicts")

        # 规则频道
        if event_type.startswith("rule."):
            channels.add("rules")

        return list(channels)

    async def subscribe(self, channels: list[str]) -> asyncio.Queue[Event]:
        """订阅频道列表，返回事件队列"""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_MAXSIZE)
        async with self._lock:
            for channel in channels:
                self._subscribers[channel].append(queue)
        return queue

    async def unsubscribe(self, channels: list[str], queue: asyncio.Queue[Event]) -> None:
        """取消订阅"""
        async with self._lock:
            for channel in channels:
                if channel in self._subscribers:
                    try:
                        self._subscribers[channel].remove(queue)
                    except ValueError:
                        pass
                    if not self._subscribers[channel]:
                        del self._subscribers[channel]

    def on(self, event_type: str, handler: Callable[[Event], Awaitable[None]]) -> None:
        """注册事件处理器"""
        self._handlers[event_type].append(handler)

    def off(self, event_type: str, handler: Callable[[Event], Awaitable[None]]) -> None:
        """移除事件处理器"""
        if event_type in self._handlers:
            try:
                self._handlers[event_type].remove(handler)
            except ValueError:
                pass


# 全局事件总线实例
event_bus = EventBus()
