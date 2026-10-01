"""WebSocket实时通信路由"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.event_bus import event_bus
from config import WEBSOCKET_MAX_CHANNELS, WEBSOCKET_MAX_MESSAGE_BYTES

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket端点 - 支持实时事件推送

    客户端连接后可发送订阅请求：
    {
        "action": "subscribe",
        "channels": ["tasks", "entries:design", "agent:xxx"]
    }

    服务端推送格式：
    {
        "event": "task.created",
        "data": {...},
        "timestamp": "..."
    }
    """
    await websocket.accept()

    # 使用可变容器存储当前订阅状态，避免并发任务间的引用问题
    state: dict = {
        "channels": [],
        "queue": None,
        "reconnect_event": asyncio.Event(),  # 通知send任务queue已变更
    }

    async def resubscribe(channels: list[str]) -> None:
        """重新订阅频道，更新共享状态"""
        # 取消旧订阅
        if state["channels"] and state["queue"]:
            await event_bus.unsubscribe(state["channels"], state["queue"])
        # 订阅新频道
        state["channels"] = channels
        state["queue"] = await event_bus.subscribe(channels)
        # 通知send任务queue已更新
        state["reconnect_event"].set()

    try:
        # 默认订阅系统事件
        await resubscribe(["task.created", "conflict.detected"])

        async def receive():
            while True:
                raw = await websocket.receive_text()
                if len(raw.encode("utf-8")) > WEBSOCKET_MAX_MESSAGE_BYTES:
                    await websocket.send_json({
                        "event": "error",
                        "data": {"message": "Message too large"},
                    })
                    await websocket.close(code=1009)
                    return
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await websocket.send_json({
                        "event": "error",
                        "data": {"message": "Invalid JSON"},
                    })
                    continue

                if not isinstance(message, dict):
                    await websocket.send_json({
                        "event": "error",
                        "data": {"message": "Message must be a JSON object"},
                    })
                    continue

                action = message.get("action")
                if action == "subscribe":
                    new_channels = message.get("channels", [])
                    if (
                        not isinstance(new_channels, list)
                        or len(new_channels) > WEBSOCKET_MAX_CHANNELS
                        or any(
                            not isinstance(channel, str)
                            or not channel.strip()
                            or len(channel) > 128
                            for channel in new_channels
                        )
                    ):
                        await websocket.send_json({
                            "event": "error",
                            "data": {"message": "Invalid channel list"},
                        })
                        continue
                    # 去重但保持客户端提交顺序，避免同一队列被重复注册。
                    new_channels = list(dict.fromkeys(
                        channel.strip() for channel in new_channels
                    ))
                    await resubscribe(new_channels)
                    await websocket.send_json({
                        "event": "subscribed",
                        "data": {"channels": new_channels},
                    })
                elif action == "ping":
                    await websocket.send_json({"event": "pong", "data": {}})
                else:
                    await websocket.send_json({
                        "event": "error",
                        "data": {"message": f"Unknown action: {action}"},
                    })

        async def send():
            while True:
                queue = state["queue"]
                if queue is None:
                    await state["reconnect_event"].wait()
                    state["reconnect_event"].clear()
                    continue

                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    await websocket.send_json({
                        "event": event.event_type,
                        "data": event.data,
                        "timestamp": event.timestamp.isoformat(),
                    })
                except asyncio.TimeoutError:
                    # 超时后检查queue是否变更
                    if state["reconnect_event"].is_set():
                        state["reconnect_event"].clear()
                    continue

        receive_task = asyncio.create_task(receive())
        send_task = asyncio.create_task(send())

        done, pending = await asyncio.wait(
            [receive_task, send_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # asyncio.wait 只返回已完成任务，不会自动消费其异常。
        # 显式 await 可避免正常断连产生「Task exception was never retrieved」。
        for task in done:
            try:
                await task
            except WebSocketDisconnect:
                pass

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.warning("WebSocket error: %s", e, exc_info=True)
    finally:
        if state["channels"] and state["queue"]:
            await event_bus.unsubscribe(state["channels"], state["queue"])
        try:
            await websocket.close()
        except RuntimeError:
            # 主动以 1009 关闭超大消息后，Starlette 不允许再发送 close。
            pass
