"""黑板核心服务测试"""
import pytest
from app.models import AgentCreate, EntryCreate, EntryUpdate, Priority
from app.blackboard import VersionConflictError


@pytest.mark.asyncio
async def test_create_entry(agent_registry, blackboard):
    """测试创建知识条目"""
    agent = await agent_registry.register(AgentCreate(name="Author"))
    entry = await blackboard.create_entry(
        agent.agent_id,
        EntryCreate(topic="design", content={"key": "value"}, tags=["test"]),
    )
    assert entry.entry_id is not None
    assert entry.topic == "design"
    assert entry.version == 1
    assert entry.author_id == agent.agent_id


@pytest.mark.asyncio
async def test_create_entry_invalid_author(blackboard):
    """测试无效作者创建条目"""
    with pytest.raises(ValueError):
        await blackboard.create_entry(
            "nonexistent",
            EntryCreate(topic="test", content={}),
        )


@pytest.mark.asyncio
async def test_update_entry_optimistic_lock(agent_registry, blackboard):
    """测试乐观锁更新"""
    agent = await agent_registry.register(AgentCreate(name="Author"))
    entry = await blackboard.create_entry(
        agent.agent_id, EntryCreate(topic="design", content={"v": 1})
    )

    # 正确版本更新
    updated = await blackboard.update_entry(
        entry.entry_id, agent.agent_id,
        EntryUpdate(content={"v": 2}, version=1),
    )
    assert updated.version == 2
    assert updated.content == {"v": 2}

    # 过时版本应该失败
    with pytest.raises(VersionConflictError) as exc_info:
        await blackboard.update_entry(
            entry.entry_id, agent.agent_id,
            EntryUpdate(content={"v": 3}, version=1),  # 旧版本
        )
    assert exc_info.value.current_version == 2


@pytest.mark.asyncio
async def test_list_entries_by_topic(agent_registry, blackboard):
    """测试按主题列出条目"""
    agent = await agent_registry.register(AgentCreate(name="Author"))
    await blackboard.create_entry(agent.agent_id, EntryCreate(topic="design", content={}))
    await blackboard.create_entry(agent.agent_id, EntryCreate(topic="code", content={}))
    await blackboard.create_entry(agent.agent_id, EntryCreate(topic="design", content={}))

    design_entries = await blackboard.list_entries(topic="design")
    assert len(design_entries) == 2

    all_entries = await blackboard.list_entries()
    assert len(all_entries) == 3


@pytest.mark.asyncio
async def test_delete_entry(agent_registry, blackboard):
    """测试删除条目"""
    agent = await agent_registry.register(AgentCreate(name="Author"))
    entry = await blackboard.create_entry(
        agent.agent_id, EntryCreate(topic="design", content={})
    )

    # 非作者不能删除
    with pytest.raises(PermissionError):
        await blackboard.delete_entry(entry.entry_id, "other_agent")

    # 作者可以删除
    success = await blackboard.delete_entry(entry.entry_id, agent.agent_id)
    assert success is True
    assert await blackboard.get_entry(entry.entry_id) is None


@pytest.mark.asyncio
async def test_merge_entries(agent_registry, blackboard):
    """测试合并条目"""
    agent = await agent_registry.register(AgentCreate(name="Author"))
    e1 = await blackboard.create_entry(
        agent.agent_id, EntryCreate(topic="design", content={"a": 1}, confidence=0.8, tags=["x"])
    )
    e2 = await blackboard.create_entry(
        agent.agent_id, EntryCreate(topic="design", content={"b": 2}, confidence=0.6, tags=["y"])
    )

    merged = await blackboard.merge_entries(
        [e1.entry_id, e2.entry_id], "merged_design", agent.agent_id
    )
    assert merged.topic == "merged_design"
    assert merged.confidence == pytest.approx(0.7, abs=0.01)
    assert set(merged.tags) == {"x", "y"}
