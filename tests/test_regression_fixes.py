"""
代码审查修复回归测试

对应 2026-09-30 代码审查报告中的缺陷编号（S=严重, M=中等）。
每个用例都对应一个曾经实测确认的缺陷，防止回归。
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.models import (
    AgentCreate, TaskCreate, TaskUpdate, EntryCreate, Priority,
    KnowledgeEntry, RuleCreate, RuleTrigger, RuleAction, ConflictStatus,
)


# ---------------------------------------------------------------- S1

@pytest.mark.asyncio
async def test_update_rule_rejects_illegal_field(rule_engine):
    """S1: 更新规则时非法字段必须被拒绝，不能写坏数据"""
    rule = await rule_engine.create_rule(
        RuleCreate(
            name="orig",
            trigger=RuleTrigger.ON_TASK_CREATED,
            action=RuleAction.NOTIFY,
            priority=1,
        )
    )

    # 主键注入
    with pytest.raises(ValueError, match="not allowed"):
        await rule_engine.update_rule(rule.rule_id, {"rule_id": "HACKED"})

    # 枚举骨架注入
    with pytest.raises(ValueError, match="not allowed"):
        await rule_engine.update_rule(rule.rule_id, {"trigger": "bad_trigger"})
    with pytest.raises(ValueError, match="not allowed"):
        await rule_engine.update_rule(rule.rule_id, {"action": "bad_action"})


@pytest.mark.asyncio
async def test_update_rule_rejects_wrong_type(rule_engine):
    """S1: 类型错误必须被拦住，否则脏数据落库会导致 list_rules 崩溃"""
    rule = await rule_engine.create_rule(
        RuleCreate(
            name="orig",
            trigger=RuleTrigger.ON_TASK_CREATED,
            action=RuleAction.NOTIFY,
            priority=1,
        )
    )

    with pytest.raises(ValueError, match="Invalid rule payload"):
        await rule_engine.update_rule(rule.rule_id, {"priority": "NOT_AN_INT"})

    # 关键：规则表必须仍然可读（原缺陷会让整表读取崩溃）
    rules = await rule_engine.list_rules()
    assert len(rules) == 1
    assert rules[0].priority == 1


@pytest.mark.asyncio
async def test_update_rule_happy_path(rule_engine):
    """S1: 合法更新仍应正常工作"""
    rule = await rule_engine.create_rule(
        RuleCreate(
            name="orig",
            trigger=RuleTrigger.ON_TASK_CREATED,
            action=RuleAction.NOTIFY,
            priority=1,
        )
    )
    updated = await rule_engine.update_rule(
        rule.rule_id, {"name": "renamed", "priority": 99}
    )
    assert updated.name == "renamed"
    assert updated.priority == 99
    # 未提交的字段保持不变
    assert updated.trigger == RuleTrigger.ON_TASK_CREATED


# ---------------------------------------------------------------- S2

@pytest.mark.asyncio
async def test_task_count_concurrent_accuracy(agent_registry, task_coordinator):
    """S2: 并发分配后负载计数必须准确（原实现会丢计数）"""
    agent = await agent_registry.register(
        AgentCreate(name="Worker", capabilities=["python"])
    )

    tasks = [
        await task_coordinator.create_task(
            TaskCreate(title=f"t{i}", required_capabilities=["python"])
        )
        for i in range(10)
    ]
    await asyncio.gather(*[task_coordinator.auto_assign(t.task_id) for t in tasks])

    refreshed = await agent_registry.get(agent.agent_id)
    assigned = await task_coordinator.get_task_assignments(agent.agent_id)
    assert len(assigned) == 10
    assert refreshed.current_task_count == 10


@pytest.mark.asyncio
async def test_assign_same_agent_is_idempotent(agent_registry, task_coordinator):
    """S2: 重复分配给同一智能体不应重复累加计数"""
    agent = await agent_registry.register(
        AgentCreate(name="Worker", capabilities=["python"])
    )
    task = await task_coordinator.create_task(
        TaskCreate(title="t", required_capabilities=["python"])
    )

    await task_coordinator.assign_task(task.task_id, agent.agent_id)
    await task_coordinator.assign_task(task.task_id, agent.agent_id)

    refreshed = await agent_registry.get(agent.agent_id)
    assert refreshed.current_task_count == 1


@pytest.mark.asyncio
async def test_decrement_task_count_floor_at_zero(agent_registry):
    """S2: 计数下限钳制为 0，不应出现负数"""
    agent = await agent_registry.register(AgentCreate(name="W"))
    await agent_registry.decrement_task_count(agent.agent_id)
    refreshed = await agent_registry.get(agent.agent_id)
    assert refreshed.current_task_count == 0


# ---------------------------------------------------------------- S3

@pytest.mark.asyncio
async def test_opinion_conflict_no_storm(
    agent_registry, blackboard, conflict_resolver
):
    """S3: 同主题多次写入只应产生一条未终结冲突（原实现会翻倍）"""
    a1 = await agent_registry.register(AgentCreate(name="A1"))
    a2 = await agent_registry.register(AgentCreate(name="A2"))

    for i in range(5):
        author = a1 if i % 2 == 0 else a2
        await blackboard.create_entry(
            author.agent_id, EntryCreate(topic="design", content={"n": i})
        )
        await conflict_resolver.detect_opinion_conflict("design")

    conflicts = await conflict_resolver.list_conflicts()
    opinion = [c for c in conflicts if c.conflict_type.value == "opinion_conflict"]
    assert len(opinion) == 1


@pytest.mark.asyncio
async def test_opinion_conflict_refreshes_entry_ids(
    agent_registry, blackboard, conflict_resolver
):
    """S3: 复用冲突时上下文里的条目集合应被刷新"""
    a1 = await agent_registry.register(AgentCreate(name="A1"))
    a2 = await agent_registry.register(AgentCreate(name="A2"))

    await blackboard.create_entry(a1.agent_id, EntryCreate(topic="t", content={"a": 1}))
    await blackboard.create_entry(a2.agent_id, EntryCreate(topic="t", content={"b": 2}))
    first = await conflict_resolver.detect_opinion_conflict("t")

    await blackboard.create_entry(a1.agent_id, EntryCreate(topic="t", content={"c": 3}))
    second = await conflict_resolver.detect_opinion_conflict("t")

    assert first.conflict_id == second.conflict_id
    assert len(second.context["entry_ids"]) == 3


# ---------------------------------------------------------------- S4

@pytest.mark.asyncio
async def test_resolve_conflict_twice_rejected(
    agent_registry, blackboard, conflict_resolver
):
    """S4: 已解决的冲突不允许重复解决（原实现会重复新建 merged 条目）"""
    a1 = await agent_registry.register(AgentCreate(name="A1"))
    a2 = await agent_registry.register(AgentCreate(name="A2"))

    await blackboard.create_entry(a1.agent_id, EntryCreate(topic="t", content={"a": 1}))
    await blackboard.create_entry(a2.agent_id, EntryCreate(topic="t", content={"b": 2}))
    conflict = await conflict_resolver.detect_opinion_conflict("t")

    await conflict_resolver.resolve_conflict(conflict.conflict_id)

    with pytest.raises(ValueError, match="already"):
        await conflict_resolver.resolve_conflict(conflict.conflict_id)


@pytest.mark.asyncio
async def test_resolve_creates_single_merged_entry(
    agent_registry, blackboard, conflict_resolver
):
    """S4: 解决一次只应产生一个 merged 条目"""
    a1 = await agent_registry.register(AgentCreate(name="A1"))
    a2 = await agent_registry.register(AgentCreate(name="A2"))

    await blackboard.create_entry(a1.agent_id, EntryCreate(topic="t", content={"a": 1}))
    await blackboard.create_entry(a2.agent_id, EntryCreate(topic="t", content={"b": 2}))
    conflict = await conflict_resolver.detect_opinion_conflict("t")
    await conflict_resolver.resolve_conflict(conflict.conflict_id)

    entries = await blackboard.list_entries()
    merged = [e for e in entries if "merged_from" in str(e.content)]
    assert len(merged) == 1


# ---------------------------------------------------------------- M1

@pytest.mark.asyncio
async def test_heartbeat_stale_marks_offline(agent_registry):
    """M1: 心跳超时的智能体应被标记离线"""
    agent = await agent_registry.register(AgentCreate(name="A"))
    await agent_registry.storage.save_agent(
        (await agent_registry.get(agent.agent_id)).model_copy(
            update={"last_heartbeat": datetime.now(timezone.utc) - timedelta(seconds=999)}
        )
    )
    await agent_registry._check_heartbeats()
    assert (await agent_registry.get(agent.agent_id)).status.value == "offline"


@pytest.mark.asyncio
async def test_heartbeat_fresh_not_marked(agent_registry):
    """M1: 心跳新鲜的智能体不应被误标离线（条件更新防竞态）"""
    agent = await agent_registry.register(AgentCreate(name="A"))
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=999)
    ).isoformat()
    changed = await agent_registry.storage.mark_agent_offline_if_stale(
        agent.agent_id, cutoff
    )
    assert changed is False
    assert (await agent_registry.get(agent.agent_id)).status.value == "online"


# ---------------------------------------------------------------- M2

@pytest.mark.asyncio
async def test_merge_entries_priority_semantic_order(agent_registry, blackboard):
    """M2: 合并优先级必须按语义序，而非字母序（原实现 LOW 会胜过 CRITICAL）"""
    a = await agent_registry.register(AgentCreate(name="A"))
    low = await blackboard.create_entry(
        a.agent_id, EntryCreate(topic="t", content={"a": 1}, priority=Priority.LOW)
    )
    critical = await blackboard.create_entry(
        a.agent_id, EntryCreate(topic="t", content={"b": 2}, priority=Priority.CRITICAL)
    )

    merged = await blackboard.merge_entries(
        [low.entry_id, critical.entry_id], "merged", a.agent_id
    )
    assert merged.priority == Priority.CRITICAL


@pytest.mark.asyncio
async def test_priority_rank_ordering():
    """M2: 优先级序数映射正确"""
    from app.models import priority_rank

    assert priority_rank(Priority.LOW) < priority_rank(Priority.NORMAL)
    assert priority_rank(Priority.NORMAL) < priority_rank(Priority.HIGH)
    assert priority_rank(Priority.HIGH) < priority_rank(Priority.CRITICAL)


# ---------------------------------------------------------------- M3

@pytest.mark.asyncio
async def test_task_result_can_be_cleared(agent_registry, task_coordinator):
    """M3: result 应可显式置空（原实现区分不了「传 None」与「没传」）"""
    agent = await agent_registry.register(AgentCreate(name="A"))
    task = await task_coordinator.create_task(TaskCreate(title="t"))

    await task_coordinator.update_task(
        task.task_id, agent.agent_id, TaskUpdate(result={"done": True})
    )
    assert (await task_coordinator.get_task(task.task_id)).result == {"done": True}

    await task_coordinator.update_task(
        task.task_id, agent.agent_id, TaskUpdate(result=None)
    )
    assert (await task_coordinator.get_task(task.task_id)).result is None


@pytest.mark.asyncio
async def test_task_update_untouched_fields_preserved(
    agent_registry, task_coordinator
):
    """M3: 未提交的字段不应被覆盖"""
    agent = await agent_registry.register(AgentCreate(name="A"))
    task = await task_coordinator.create_task(
        TaskCreate(title="t", description="orig", required_capabilities=["python"])
    )
    await task_coordinator.update_task(
        task.task_id, agent.agent_id, TaskUpdate(priority=Priority.HIGH)
    )
    refreshed = await task_coordinator.get_task(task.task_id)
    assert refreshed.description == "orig"
    assert refreshed.required_capabilities == ["python"]
    assert refreshed.priority == Priority.HIGH


# ---------------------------------------------------------------- M4

@pytest.mark.asyncio
async def test_datetimes_are_timezone_aware(agent_registry, blackboard, conflict_resolver):
    """M4: 时间字段应带时区信息（原实现为 naive datetime）"""
    agent = await agent_registry.register(AgentCreate(name="A"))
    assert agent.registered_at.tzinfo is not None

    entry = await blackboard.create_entry(
        agent.agent_id, EntryCreate(topic="t", content={"x": 1})
    )
    # 经存储往返后仍应带时区
    reloaded = await blackboard.get_entry(entry.entry_id)
    assert reloaded.created_at.tzinfo is not None


# ---------------------------------------------------------------- M5

@pytest.mark.asyncio
async def test_non_json_serializable_content(storage, agent_registry):
    """M5: content 含非 JSON 原生类型时应兜底而非崩溃"""
    agent = await agent_registry.register(AgentCreate(name="A"))
    entry = KnowledgeEntry(
        entry_id="e-m5",
        topic="t",
        content={"when": datetime.now(timezone.utc), "tags": {"a", "b"}},
        author_id=agent.agent_id,
    )
    await storage.save_entry(entry)
    reloaded = await storage.get_entry("e-m5")
    assert reloaded is not None
    # datetime 被降级为字符串，不抛异常
    assert isinstance(reloaded.content["when"], str)


# ---------------------------------------------------------------- M6

@pytest.mark.asyncio
async def test_event_bus_counts_dropped_events():
    """M6: 队列满时应记录丢弃计数，而非静默丢弃"""
    from app.event_bus import event_bus, SUBSCRIBER_QUEUE_MAXSIZE

    queue = await event_bus.subscribe(["m6-test"])
    before = event_bus.dropped_events
    try:
        for i in range(SUBSCRIBER_QUEUE_MAXSIZE + 50):
            await event_bus.publish("m6-test", {"i": i})
        assert queue.qsize() == SUBSCRIBER_QUEUE_MAXSIZE
        assert event_bus.dropped_events >= before + 50
    finally:
        await event_bus.unsubscribe(["m6-test"], queue)


# ---------------------------------------------------------------- M7

@pytest.mark.asyncio
async def test_indexes_created(storage):
    """M7: 关键查询索引应已建立"""
    async with storage.db.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
    ) as cursor:
        names = {row[0] for row in await cursor.fetchall()}

    for idx in (
        "idx_tasks_assignee",
        "idx_agents_status",
        "idx_agents_heartbeat",
        "idx_conflicts_status",
        "idx_conflicts_type_status",
        "idx_tasks_priority_status",
    ):
        assert idx in names, f"缺少索引 {idx}"


# ---------------------------------------------------------------- N7

@pytest.mark.asyncio
async def test_find_candidates_stable_ordering(agent_registry):
    """N3: 同分候选应按 agent_id 稳定排序，保证结果可复现"""
    for name in ("Zeta", "Alpha", "Beta"):
        await agent_registry.register(
            AgentCreate(name=name, capabilities=["python"])
        )
    first = await agent_registry.find_candidates(["python"])
    second = await agent_registry.find_candidates(["python"])
    assert [a.agent_id for a in first] == [a.agent_id for a in second]
    assert first == sorted(first, key=lambda a: a.agent_id)
