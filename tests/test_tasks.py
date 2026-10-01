"""任务协调服务测试"""
import pytest
from app.models import AgentCreate, TaskCreate, TaskUpdate, TaskStatus, Priority
from app.task_coordinator import CircularDependencyError


@pytest.mark.asyncio
async def test_create_task(agent_registry, task_coordinator):
    """测试任务创建"""
    creator = await agent_registry.register(AgentCreate(name="Creator"))
    task = await task_coordinator.create_task(
        TaskCreate(
            title="Test Task",
            description="A test task",
            required_capabilities=["python"],
            creator_id=creator.agent_id,
        )
    )
    assert task.task_id is not None
    assert task.status == TaskStatus.PENDING
    assert task.creator_id == creator.agent_id


@pytest.mark.asyncio
async def test_create_task_invalid_creator(task_coordinator):
    """测试无效创建者"""
    with pytest.raises(ValueError):
        await task_coordinator.create_task(
            TaskCreate(title="Test", creator_id="nonexistent")
        )


@pytest.mark.asyncio
async def test_auto_assign(agent_registry, task_coordinator, populated_agents):
    """测试自动分配"""
    creator = populated_agents[0]
    task = await task_coordinator.create_task(
        TaskCreate(
            title="Code Task",
            required_capabilities=["code_gen", "python"],
            creator_id=creator.agent_id,
        )
    )

    assigned = await task_coordinator.auto_assign(task.task_id)
    assert assigned is not None
    assert assigned.status == TaskStatus.ASSIGNED
    assert assigned.assignee_id is not None

    # 验证分配给了CodeAgent (第一个有code_gen能力的)
    agents = await agent_registry.list_all()
    assignee = next(a for a in agents if a.agent_id == assigned.assignee_id)
    assert "code_gen" in assignee.capabilities


@pytest.mark.asyncio
async def test_auto_assign_no_candidates(agent_registry, task_coordinator):
    """测试无候选智能体时自动分配"""
    creator = await agent_registry.register(AgentCreate(name="Creator"))
    task = await task_coordinator.create_task(
        TaskCreate(
            title="Impossible Task",
            required_capabilities=["quantum_computing"],
            creator_id=creator.agent_id,
        )
    )
    result = await task_coordinator.auto_assign(task.task_id)
    assert result is None


@pytest.mark.asyncio
async def test_task_dependencies(agent_registry, task_coordinator, populated_agents):
    """测试任务依赖"""
    creator = populated_agents[0]
    agents = populated_agents

    # 创建父任务并完成
    parent = await task_coordinator.create_task(
        TaskCreate(title="Parent", required_capabilities=["python"], creator_id=creator.agent_id)
    )
    await task_coordinator.assign_task(parent.task_id, agents[1].agent_id)
    await task_coordinator.update_task(
        parent.task_id, agents[1].agent_id,
        TaskUpdate(status=TaskStatus.DONE),
    )

    # 创建依赖父任务的子任务
    child = await task_coordinator.create_task(
        TaskCreate(
            title="Child",
            required_capabilities=["python"],
            dependencies=[parent.task_id],
            creator_id=creator.agent_id,
        )
    )

    # 依赖已满足，可以分配
    assigned = await task_coordinator.auto_assign(child.task_id)
    assert assigned is not None


@pytest.mark.asyncio
async def test_circular_dependency_detection(agent_registry, task_coordinator, storage):
    """测试循环依赖检测"""
    creator = await agent_registry.register(AgentCreate(name="Creator"))

    # 创建三个任务
    t1 = await task_coordinator.create_task(
        TaskCreate(title="T1", creator_id=creator.agent_id)
    )
    t2 = await task_coordinator.create_task(
        TaskCreate(title="T2", creator_id=creator.agent_id)
    )
    t3 = await task_coordinator.create_task(
        TaskCreate(title="T3", creator_id=creator.agent_id)
    )

    # 直接通过存储层设置循环依赖: T1->T2->T3->T1
    t1.dependencies = [t2.task_id]
    await storage.save_task(t1)
    t2.dependencies = [t3.task_id]
    await storage.save_task(t2)
    t3.dependencies = [t1.task_id]
    await storage.save_task(t3)

    # 检测循环依赖应该抛出异常
    with pytest.raises(CircularDependencyError):
        await task_coordinator._detect_circular_dependencies()
        # 这里先创建t3依赖t2不会报错，需要更复杂的测试


@pytest.mark.asyncio
async def test_task_status_flow(agent_registry, task_coordinator, populated_agents):
    """测试任务状态流转"""
    creator = populated_agents[0]
    agent = populated_agents[1]
    task = await task_coordinator.create_task(
        TaskCreate(title="Status Flow", creator_id=creator.agent_id)
    )

    await task_coordinator.assign_task(task.task_id, agent.agent_id)
    task = await task_coordinator.get_task(task.task_id)
    assert task.status == TaskStatus.ASSIGNED

    await task_coordinator.update_task(
        task.task_id, agent.agent_id, TaskUpdate(status=TaskStatus.IN_PROGRESS)
    )
    task = await task_coordinator.get_task(task.task_id)
    assert task.status == TaskStatus.IN_PROGRESS

    await task_coordinator.update_task(
        task.task_id, agent.agent_id,
        TaskUpdate(status=TaskStatus.DONE, result={"output": "done"}),
    )
    task = await task_coordinator.get_task(task.task_id)
    assert task.status == TaskStatus.DONE
    assert task.result == {"output": "done"}
