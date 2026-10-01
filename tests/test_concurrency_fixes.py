"""本轮运行评估中修复的并发缺陷回归测试

覆盖：
- 乐观锁 TOCTOU：并发更新同一 entry 只能有一个成功
- 冲突检测活跃态：ESCALATED 必须纳入幂等判定，否则冲突雪崩
- 冲突检测并发安全：并发检测同一 topic 只产生一条记录
"""
import asyncio

import pytest

from app.blackboard import VersionConflictError
from app.models import (
    AgentCreate, EntryCreate, EntryUpdate,
    CONFLICT_ACTIVE_STATUSES, CONFLICT_TERMINAL_STATUSES, ConflictStatus,
    ConflictType,
)


async def _mk_agent(storage, name):
    from app.agent_registry import AgentRegistry
    return await AgentRegistry(storage).register(AgentCreate(name=name))


# ---- 乐观锁 TOCTOU ----

@pytest.mark.asyncio
async def test_concurrent_update_only_one_wins(storage, blackboard):
    """并发更新同一 entry：应恰好 1 个成功，其余抛版本冲突"""
    author = await _mk_agent(storage, "A")
    entry = await blackboard.create_entry(
        author.agent_id, EntryCreate(topic="t", content="v0")
    )
    assert entry.version == 1

    async def bump(i: int):
        try:
            await blackboard.update_entry(
                entry.entry_id, author.agent_id,
                EntryUpdate(content=f"v{i}", version=1),
            )
            return "ok"
        except VersionConflictError:
            return "conflict"

    results = await asyncio.gather(*[bump(i) for i in range(10)])
    assert results.count("ok") == 1, f"应恰好 1 个成功，实际 {results.count('ok')} 个"
    assert results.count("conflict") == 9

    final = await storage.get_entry(entry.entry_id)
    assert final.version == 2, "版本应只推进一格"


@pytest.mark.asyncio
async def test_sequential_update_still_works(storage, blackboard):
    """串行更新应正常推进版本（确认 CAS 未影响正常路径）"""
    author = await _mk_agent(storage, "A")
    entry = await blackboard.create_entry(
        author.agent_id, EntryCreate(topic="t", content="v0")
    )
    for i in range(1, 5):
        entry = await blackboard.update_entry(
            entry.entry_id, author.agent_id,
            EntryUpdate(content=f"v{i}", version=entry.version),
        )
        assert entry.version == i + 1
    assert entry.content == "v4"


@pytest.mark.asyncio
async def test_update_wrong_version_fails_fast(storage, blackboard):
    """版本明显不匹配时应快速失败"""
    author = await _mk_agent(storage, "A")
    entry = await blackboard.create_entry(
        author.agent_id, EntryCreate(topic="t", content="v0")
    )
    with pytest.raises(VersionConflictError):
        await blackboard.update_entry(
            entry.entry_id, author.agent_id,
            EntryUpdate(content="x", version=99),
        )


# ---- 冲突状态分组 ----

def test_escalated_is_active_not_terminal():
    """ESCALATED 是活跃态（问题仍在），不能算终态"""
    assert ConflictStatus.ESCALATED in CONFLICT_ACTIVE_STATUSES
    assert ConflictStatus.ESCALATED not in CONFLICT_TERMINAL_STATUSES


def test_active_and_terminal_are_disjoint_and_complete():
    """活跃态与终态互斥，且并集覆盖全部状态"""
    assert not (CONFLICT_ACTIVE_STATUSES & CONFLICT_TERMINAL_STATUSES)
    assert CONFLICT_ACTIVE_STATUSES | CONFLICT_TERMINAL_STATUSES == set(ConflictStatus)


# ---- 冲突雪崩 ----

@pytest.mark.asyncio
async def test_repeated_writes_do_not_avalanche_conflicts(storage, blackboard, conflict_resolver):
    """同一 topic 反复写入（每轮都检测）只应产生 1 条意见冲突

    注：conftest 的 conflict_resolver 夹具是手工拼装的，未挂载
    ServiceContainer 中「entry.created → 冲突检测」的事件回调，
    因此此处显式调用检测，聚焦验证幂等语义本身。
    """
    a1 = await _mk_agent(storage, "A")
    a2 = await _mk_agent(storage, "B")
    for i in range(15):
        author = a1.agent_id if i % 2 == 0 else a2.agent_id
        await blackboard.create_entry(
            author, EntryCreate(topic="storm", content=f"v{i}")
        )
        # 模拟生产路径：每条写入后触发一次检测
        await conflict_resolver.detect_opinion_conflict("storm")

    conflicts = await conflict_resolver.list_conflicts()
    opc = [c for c in conflicts
           if c.conflict_type == ConflictType.OPINION_CONFLICT
           and c.context.get("topic") == "storm"]
    assert len(opc) == 1, f"应只有 1 条冲突，实际 {len(opc)} 条"
    # 上下文应刷新到最新集合
    assert len(opc[0].context.get("entry_ids", [])) == 15


@pytest.mark.asyncio
async def test_escalated_conflict_reused_not_duplicated(storage, blackboard, conflict_resolver):
    """已升级为 ESCALATED 的冲突应被复用，而非新建"""
    from app.models import ConflictStatus as CS
    a1 = await _mk_agent(storage, "A")
    a2 = await _mk_agent(storage, "B")
    await blackboard.create_entry(a1.agent_id, EntryCreate(topic="k", content="x1"))
    await blackboard.create_entry(a2.agent_id, EntryCreate(topic="k", content="x2"))

    first = await conflict_resolver.detect_opinion_conflict("k")
    # 手动升级（模拟规则引擎 escalate 行为）
    first.status = CS.ESCALATED
    await storage.save_conflict(first)

    again = await conflict_resolver.detect_opinion_conflict("k")
    assert again.conflict_id == first.conflict_id, "应复用已升级的冲突"

    allc = await conflict_resolver.list_conflicts()
    assert len(allc) == 1


@pytest.mark.asyncio
async def test_concurrent_detect_produces_single_conflict(storage, blackboard, conflict_resolver):
    """并发检测同一 topic 只应产生 1 条冲突（锁保护临界区）"""
    a1 = await _mk_agent(storage, "A")
    a2 = await _mk_agent(storage, "B")
    await blackboard.create_entry(a1.agent_id, EntryCreate(topic="c", content="x1"))
    await blackboard.create_entry(a2.agent_id, EntryCreate(topic="c", content="x2"))

    await asyncio.gather(*[
        conflict_resolver.detect_opinion_conflict("c") for _ in range(12)
    ])
    allc = await conflict_resolver.list_conflicts()
    assert len(allc) == 1, f"并发检测应只产生 1 条，实际 {len(allc)} 条"


@pytest.mark.asyncio
async def test_different_topics_do_not_block_each_other(storage, blackboard, conflict_resolver):
    """不同 topic 的检测互不阻塞（锁按 topic 细分）"""
    a1 = await _mk_agent(storage, "A")
    a2 = await _mk_agent(storage, "B")
    for t in ("t1", "t2", "t3"):
        await blackboard.create_entry(a1.agent_id, EntryCreate(topic=t, content="a"))
        await blackboard.create_entry(a2.agent_id, EntryCreate(topic=t, content="b"))

    await asyncio.gather(*[
        conflict_resolver.detect_opinion_conflict(t) for t in ("t1", "t2", "t3")
    ])
    allc = await conflict_resolver.list_conflicts()
    topics = sorted(c.context.get("topic") for c in allc)
    assert topics == ["t1", "t2", "t3"]
