"""冲突解决器测试"""
import pytest
from app.models import (
    AgentCreate, EntryCreate, TaskCreate, TaskStatus,
    ConflictType, ConflictStatus,
)


@pytest.mark.asyncio
async def test_detect_write_conflict(agent_registry, blackboard, conflict_resolver):
    """测试写冲突检测"""
    author = await agent_registry.register(AgentCreate(name="Author"))
    other = await agent_registry.register(AgentCreate(name="Other"))

    entry = await blackboard.create_entry(
        author.agent_id, EntryCreate(topic="design", content={"v": 1})
    )

    conflict = await conflict_resolver.detect_write_conflict(
        entry.entry_id, other.agent_id, current_version=1
    )
    assert conflict is not None
    assert conflict.conflict_type == ConflictType.WRITE_CONFLICT
    assert conflict.status == ConflictStatus.DETECTED
    assert entry.entry_id in conflict.description


@pytest.mark.asyncio
async def test_detect_opinion_conflict(agent_registry, blackboard, conflict_resolver):
    """测试意见冲突检测"""
    author1 = await agent_registry.register(AgentCreate(name="Author1"))
    author2 = await agent_registry.register(AgentCreate(name="Author2"))

    await blackboard.create_entry(
        author1.agent_id,
        EntryCreate(topic="design", content={"approach": "microservice"}, confidence=0.9),
    )
    await blackboard.create_entry(
        author2.agent_id,
        EntryCreate(topic="design", content={"approach": "monolith"}, confidence=0.7),
    )

    conflict = await conflict_resolver.detect_opinion_conflict("design")
    assert conflict is not None
    assert conflict.conflict_type == ConflictType.OPINION_CONFLICT
    assert len(conflict.involved_agents) == 2


@pytest.mark.asyncio
async def test_detect_opinion_no_conflict(agent_registry, blackboard, conflict_resolver):
    """测试无意见冲突"""
    author = await agent_registry.register(AgentCreate(name="Author"))
    await blackboard.create_entry(
        author.agent_id, EntryCreate(topic="design", content={"only": "one"})
    )
    conflict = await conflict_resolver.detect_opinion_conflict("design")
    assert conflict is None


@pytest.mark.asyncio
async def test_resolve_write_conflict_by_merge(
    agent_registry, blackboard, conflict_resolver
):
    """测试写冲突合并解决"""
    author = await agent_registry.register(AgentCreate(name="Author"))
    entry = await blackboard.create_entry(
        author.agent_id, EntryCreate(topic="design", content={"v": 1})
    )

    conflict = await conflict_resolver.detect_write_conflict(
        entry.entry_id, "other", current_version=1
    )
    resolved = await conflict_resolver.resolve_conflict(
        conflict.conflict_id, strategy="merge"
    )
    assert resolved.status == ConflictStatus.RESOLVED
    assert "version" in resolved.resolution.lower() or "merge" in resolved.resolution.lower()


@pytest.mark.asyncio
async def test_resolve_opinion_conflict_by_vote(
    agent_registry, blackboard, conflict_resolver
):
    """测试意见冲突投票解决"""
    author1 = await agent_registry.register(AgentCreate(name="Author1"))
    author2 = await agent_registry.register(AgentCreate(name="Author2"))

    e1 = await blackboard.create_entry(
        author1.agent_id,
        EntryCreate(topic="design", content={"approach": "A"}, confidence=0.9),
    )
    e2 = await blackboard.create_entry(
        author2.agent_id,
        EntryCreate(topic="design", content={"approach": "B"}, confidence=0.6),
    )

    conflict = await conflict_resolver.detect_opinion_conflict("design")
    resolved = await conflict_resolver.resolve_conflict(
        conflict.conflict_id, strategy="vote"
    )
    assert resolved.status == ConflictStatus.RESOLVED
    assert "vote" in resolved.resolution.lower() or "confidence" in resolved.resolution.lower()


@pytest.mark.asyncio
async def test_conflict_resolution_escalate(conflict_resolver):
    """测试冲突升级"""
    from app.models import Conflict
    conflict = Conflict(
        conflict_id="test-conflict",
        conflict_type=ConflictType.RESOURCE_CONFLICT,
        description="Test escalation",
    )
    await conflict_resolver.storage.save_conflict(conflict)

    resolved = await conflict_resolver.resolve_conflict(
        conflict.conflict_id, strategy="escalate"
    )
    assert resolved.status == ConflictStatus.ESCALATED
