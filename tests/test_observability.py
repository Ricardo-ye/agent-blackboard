"""关联 ID 与运行时观测的契约测试。"""
from __future__ import annotations

import uuid

import httpx
import pytest

from app.event_bus import EventBus
from app.observability import (
    REQUEST_ID_HEADER,
    request_metrics,
    request_trace_id,
    reset_trace_id,
    set_trace_id,
)


def test_request_trace_id_accepts_safe_client_value_and_replaces_invalid_value():
    assert request_trace_id("incident-2026.10_01") == "incident-2026.10_01"
    generated = request_trace_id("invalid trace id with spaces")
    assert generated != "invalid trace id with spaces"
    assert str(uuid.UUID(generated)) == generated


@pytest.mark.asyncio
async def test_event_bus_copies_current_trace_id_to_event():
    bus = EventBus()
    observed_trace_ids: list[str | None] = []

    async def publish_follow_up(event):
        follow_up = await bus.publish("task.assigned", {"task_id": event.data["task_id"]})
        observed_trace_ids.append(follow_up.trace_id)

    bus.on("task.created", publish_follow_up)
    token = set_trace_id("request-123")
    try:
        event = await bus.publish("task.created", {"task_id": "task-1"})
    finally:
        reset_trace_id(token)

    assert event.trace_id == "request-123"
    assert observed_trace_ids == ["request-123"]


@pytest.mark.asyncio
async def test_http_middleware_returns_trace_header_and_records_route_metric():
    import main

    request_metrics.reset()
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/", headers={REQUEST_ID_HEADER: "portfolio-proof-1"})

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER] == "portfolio-proof-1"
    payload = "\n".join(request_metrics.render_prometheus())
    assert (
        'blackboard_http_requests_total{method="GET",route="/",status="200"} 1'
        in payload
    )
    assert "blackboard_http_request_duration_seconds_bucket" in payload
    request_metrics.reset()
