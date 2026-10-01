"""P2 优化项的回归测试：事务、批量查询、合并幂等、WAL

夹具复用 conftest.py 中的 storage / blackboard（pytest_asyncio.fixture）。
"""
import pytest

from app.models import AgentCreate, EntryCreate, TaskCreate, TaskUpdate, TaskStatus


async def _mk_author(storage, name="Author"):
    from app.agent_registry import AgentRegistry
    reg = AgentRegistry(storage)
    return await reg.register(AgentCreate(name=name))


# ---- 事务 ----

@pytest.mark.asyncio
async def test_transaction_commits_on_success(storage):
    """事务块正常退出后数据应落库"""
    from app.models import KnowledgeEntry
    async with storage.transaction():
        await storage.save_entry(
            KnowledgeEntry(entry_id="tx-ok-1", topic="t", content={"a": 1}, author_id="x")
        )
    assert await storage.get_entry("tx-ok-1") is not None


@pytest.mark.asyncio
async def test_transaction_rolls_back_on_error(storage):
    """事务块抛异常时应回滚"""
    from app.models import KnowledgeEntry
    entry = KnowledgeEntry(
        entry_id="tx-rollback-1", topic="t", content={"a": 1}, author_id="nobody"
    )
    with pytest.raises(RuntimeError):
        async with storage.transaction():
            await storage.save_entry(entry)
            raise RuntimeError("boom")
    assert await storage.get_entry("tx-rollback-1") is None


@pytest.mark.asyncio
async def test_nested_transaction_only_commits_once(storage):
    """嵌套事务应只在最外层退出时提交"""
    from app.models import KnowledgeEntry
    async with storage.transaction():
        async with storage.transaction():
            await storage.save_entry(
                KnowledgeEntry(entry_id="n1", topic="t", content={}, author_id="x")
            )
        assert storage._tx_depth == 1  # 内层退出后仍在外层事务中
    assert await storage.get_entry("n1") is not None
    assert storage._tx_depth == 0


# ---- 批量查询 ----

@pytest.mark.asyncio
async def test_get_tasks_batch(storage):
    """get_tasks 应批量返回字典并忽略不存在的 id"""
    from app.models import Task
    for i in range(3):
        await storage.save_task(Task(task_id=f"t{i}", title=f"T{i}"))
    result = await storage.get_tasks(["t0", "t1", "t2", "missing"])
    assert set(result.keys()) == {"t0", "t1", "t2"}
    assert result["t1"].title == "T1"


@pytest.mark.asyncio
async def test_get_tasks_empty(storage):
    assert await storage.get_tasks([]) == {}


@pytest.mark.asyncio
async def test_get_tasks_dedups_ids(storage):
    """重复 id 不应导致 SQL 参数错位"""
    from app.models import Task
    await storage.save_task(Task(task_id="dup", title="Dup"))
    result = await storage.get_tasks(["dup", "dup", "dup"])
    assert list(result.keys()) == ["dup"]


# ---- 合并幂等 ----

@pytest.mark.asyncio
async def test_merge_entries_is_idempotent(storage, blackboard):
    """同一组 source_ids 重复合并应返回同一条目，不产生新条目"""
    author = await _mk_author(storage)
    e1 = await blackboard.create_entry(author.agent_id, EntryCreate(topic="k", content="A"))
    e2 = await blackboard.create_entry(
        author.agent_id, EntryCreate(topic="k", content="B", confidence=0.5)
    )

    m1 = await blackboard.merge_entries([e1.entry_id, e2.entry_id], "merged", author.agent_id)
    m2 = await blackboard.merge_entries([e1.entry_id, e2.entry_id], "merged", author.agent_id)
    assert m1.entry_id == m2.entry_id, "重复合并应复用既有条目"

    # 顺序颠倒也应命中同一合并条目
    m3 = await blackboard.merge_entries([e2.entry_id, e1.entry_id], "merged", author.agent_id)
    assert m3.entry_id == m1.entry_id, "source_ids 顺序不应影响幂等判定"


@pytest.mark.asyncio
async def test_merge_different_sources_creates_new(storage, blackboard):
    """不同来源集合应产生不同合并条目"""
    author = await _mk_author(storage)
    e1 = await blackboard.create_entry(author.agent_id, EntryCreate(topic="k", content="A"))
    e2 = await blackboard.create_entry(author.agent_id, EntryCreate(topic="k", content="B"))
    e3 = await blackboard.create_entry(author.agent_id, EntryCreate(topic="k", content="C"))

    m1 = await blackboard.merge_entries([e1.entry_id, e2.entry_id], "merged", author.agent_id)
    m2 = await blackboard.merge_entries([e1.entry_id, e3.entry_id], "merged", author.agent_id)
    assert m1.entry_id != m2.entry_id


@pytest.mark.asyncio
async def test_merge_priority_still_semantic(storage, blackboard):
    """幂等改造不应破坏优先级语义（CRITICAL 高于 LOW）"""
    from app.models import Priority
    author = await _mk_author(storage)
    e1 = await blackboard.create_entry(
        author.agent_id, EntryCreate(topic="k", content="A", priority=Priority.CRITICAL)
    )
    e2 = await blackboard.create_entry(
        author.agent_id, EntryCreate(topic="k", content="B", priority=Priority.LOW)
    )
    merged = await blackboard.merge_entries([e1.entry_id, e2.entry_id], "merged", author.agent_id)
    assert merged.priority == Priority.CRITICAL


# ---- 依赖检查批量化的语义等价 ----

@pytest.mark.asyncio
async def test_dependency_check_uses_batch_query(storage):
    """批量依赖检查结果应与逐个查询一致"""
    from app.task_coordinator import TaskCoordinator
    from app.agent_registry import AgentRegistry

    reg = AgentRegistry(storage)
    coord = TaskCoordinator(storage, reg)

    t1 = await coord.create_task(TaskCreate(title="dep1"))
    t2 = await coord.create_task(TaskCreate(title="dep2"))
    child = await coord.create_task(
        TaskCreate(title="child", dependencies=[t1.task_id, t2.task_id])
    )

    # 依赖未完成 -> False
    assert await coord._check_dependencies_ready(child) is False

    # 完成一个依赖 -> 仍 False
    await coord.update_task(t1.task_id, "sys", TaskUpdate(status=TaskStatus.DONE))
    child = await storage.get_task(child.task_id)
    assert await coord._check_dependencies_ready(child) is False

    # 全部完成 -> True
    await coord.update_task(t2.task_id, "sys", TaskUpdate(status=TaskStatus.DONE))
    child = await storage.get_task(child.task_id)
    assert await coord._check_dependencies_ready(child) is True


@pytest.mark.asyncio
async def test_validate_dependencies_reports_all_missing(storage):
    """缺失依赖应一次性报出，而非只报第一个"""
    from app.task_coordinator import TaskCoordinator
    from app.agent_registry import AgentRegistry

    coord = TaskCoordinator(storage, AgentRegistry(storage))
    with pytest.raises(ValueError) as ei:
        await coord.create_task(TaskCreate(title="x", dependencies=["nope1", "nope2"]))
    msg = str(ei.value)
    assert "nope1" in msg and "nope2" in msg


# ---- WAL 模式 ----

@pytest.mark.asyncio
async def test_wal_mode_enabled(storage):
    async with storage.db.execute("PRAGMA journal_mode") as cur:
        row = await cur.fetchone()
    assert str(row[0]).lower() == "wal"
