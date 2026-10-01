"""报告中可在当前 SQLite 架构内落地的优化回归测试。"""
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models import (
    AgentCreate,
    EntryCreate,
    RuleAction,
    RuleCreate,
    RuleTrigger,
    TaskCreate,
)


def test_external_input_boundaries_are_enforced():
    """安全报告要求的输入边界应在 Pydantic 入口统一拦截。"""
    with pytest.raises(ValidationError):
        AgentCreate(name="   ")
    with pytest.raises(ValidationError):
        EntryCreate(topic="", content={})
    with pytest.raises(ValidationError):
        EntryCreate(topic="t", content={}, confidence=1.01)
    with pytest.raises(ValidationError):
        EntryCreate(topic="t", content={}, confidence=-0.01)
    with pytest.raises(ValidationError):
        TaskCreate(title="x" * 129)


@pytest.mark.asyncio
async def test_batch_entry_create_uses_single_author_validation(
    storage, agent_registry, blackboard
):
    author = await agent_registry.register(AgentCreate(name="BatchAuthor"))
    items = [
        EntryCreate(topic="batch", content={"index": index})
        for index in range(25)
    ]

    created = await blackboard.create_entries_batch(author.agent_id, items)

    assert len(created) == 25
    assert len(await storage.list_entries("batch")) == 25
    assert {entry.author_id for entry in created} == {author.agent_id}


@pytest.mark.asyncio
async def test_batch_entry_create_rejects_unknown_author(blackboard):
    with pytest.raises(ValueError, match="not found"):
        await blackboard.create_entries_batch(
            "missing", [EntryCreate(topic="batch", content={})]
        )


@pytest.mark.asyncio
async def test_heartbeat_scan_is_bulk_and_preserves_fresh_agents(
    storage, agent_registry, monkeypatch
):
    stale = await agent_registry.register(AgentCreate(name="Stale"))
    fresh = await agent_registry.register(AgentCreate(name="Fresh"))
    stale.last_heartbeat = datetime.now(timezone.utc) - timedelta(hours=1)
    await storage.save_agent(stale)

    async def list_agents_must_not_run():
        raise AssertionError("heartbeat scan regressed to full list + per-row update")

    monkeypatch.setattr(storage, "list_agents", list_agents_must_not_run)
    await agent_registry._check_heartbeats()

    assert (await storage.get_agent(stale.agent_id)).status.value == "offline"
    assert (await storage.get_agent(fresh.agent_id)).status.value == "online"


@pytest.mark.asyncio
async def test_report_indexes_are_created(storage):
    async with storage.db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    ) as cursor:
        names = {row[0] for row in await cursor.fetchall()}

    assert {
        "idx_agents_status",
        "idx_agents_status_heartbeat",
        "idx_tasks_status_priority",
        "idx_entries_topic_updated",
        "idx_conflicts_status",
    } <= names


@pytest.mark.asyncio
async def test_rule_conditions_are_compiled_on_cache_reload(rule_engine):
    rule = await rule_engine.create_rule(
        RuleCreate(
            name="Compiled",
            trigger=RuleTrigger.ON_ENTRY_CREATED,
            condition={
                "and": [
                    {"field": "data.score", "operator": "gte", "value": 80},
                    {"field": "topic", "operator": "eq", "value": "review"},
                ]
            },
            action=RuleAction.NOTIFY,
        )
    )

    matcher = rule_engine._condition_cache[rule.rule_id]
    assert matcher({"data": {"score": 90}, "topic": "review"}) is True
    assert matcher({"data": {"score": 60}, "topic": "review"}) is False


@pytest.mark.asyncio
async def test_database_aggregate_stats_and_prometheus_output(
    storage, agent_registry, blackboard
):
    author = await agent_registry.register(AgentCreate(name="Metrics"))
    await blackboard.create_entry(
        author.agent_id, EntryCreate(topic="metrics", content={})
    )

    stats = await storage.get_stats()
    assert stats.total_agents == 1
    assert stats.online_agents == 1
    assert stats.total_entries == 1

    import main as main_module

    original_storage = main_module.services.storage
    main_module.services.storage = storage
    try:
        payload = await main_module.metrics()
    finally:
        main_module.services.storage = original_storage

    assert "# TYPE blackboard_agents_total gauge" in payload
    assert "blackboard_agents_total 1" in payload
    assert "blackboard_event_bus_dropped_events_total" in payload
