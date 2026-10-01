"""集成测试 - 完整协作流程测试"""
import pytest
from app.models import (
    AgentCreate, EntryCreate, TaskCreate, TaskUpdate, TaskStatus,
    EntryUpdate, Priority, RuleTrigger,
)


@pytest.mark.asyncio
async def test_full_collaboration_flow(
    agent_registry, blackboard, task_coordinator,
    conflict_resolver, rule_engine,
):
    """
    测试完整协作流程：
    1. 注册多个智能体
    2. 创建任务并自动分配
    3. 智能体读取黑板信息
    4. 智能体发布结果到黑板
    5. 任务完成
    6. 测试冲突场景
    """
    # 1. 注册智能体
    designer = await agent_registry.register(
        AgentCreate(name="Designer", capabilities=["architecture", "design"])
    )
    coder = await agent_registry.register(
        AgentCreate(name="Coder", capabilities=["code_gen", "python", "fastapi"])
    )
    tester = await agent_registry.register(
        AgentCreate(name="Tester", capabilities=["testing", "python"])
    )

    # 2. 设计师发布设计到黑板
    design_entry = await blackboard.create_entry(
        designer.agent_id,
        EntryCreate(
            topic="architecture",
            content={"pattern": "blackboard", "components": ["blackboard", "agents", "control"]},
            tags=["design", "architecture"],
            priority=Priority.HIGH,
        ),
    )
    assert design_entry.entry_id is not None

    # 3. 创建编码任务并自动分配
    coding_task = await task_coordinator.create_task(
        TaskCreate(
            title="Implement core module",
            description="Implement the blackboard core",
            required_capabilities=["code_gen", "python"],
            priority=Priority.HIGH,
            creator_id=designer.agent_id,
        )
    )

    # 自动分配应该找到Coder
    assigned = await task_coordinator.auto_assign(coding_task.task_id)
    assert assigned is not None
    assert assigned.assignee_id == coder.agent_id

    # 4. Coder读取设计并开始任务
    all_entries = await blackboard.list_entries(topic="architecture")
    assert len(all_entries) >= 1

    await task_coordinator.update_task(
        coding_task.task_id, coder.agent_id,
        TaskUpdate(status=TaskStatus.IN_PROGRESS),
    )

    # 5. Coder发布代码到黑板
    code_entry = await blackboard.create_entry(
        coder.agent_id,
        EntryCreate(
            topic="implementation",
            content={"module": "blackboard_core", "code": "..."},
            tags=["code"],
        ),
    )

    # 6. 创建测试任务，依赖编码任务
    testing_task = await task_coordinator.create_task(
        TaskCreate(
            title="Test core module",
            required_capabilities=["testing", "python"],
            dependencies=[coding_task.task_id],
            creator_id=designer.agent_id,
        )
    )

    # 测试任务不应被分配（依赖未完成）
    assigned_test = await task_coordinator.auto_assign(testing_task.task_id)
    assert assigned_test is None

    # 7. 完成编码任务
    await task_coordinator.update_task(
        coding_task.task_id, coder.agent_id,
        TaskUpdate(status=TaskStatus.DONE, result={"entry_id": code_entry.entry_id}),
    )

    # 8. 编码任务完成后，测试任务应该可以分配了
    assigned_test = await task_coordinator.auto_assign(testing_task.task_id)
    assert assigned_test is not None
    assert assigned_test.assignee_id == tester.agent_id

    # 9. 完成测试任务
    await task_coordinator.update_task(
        testing_task.task_id, tester.agent_id,
        TaskUpdate(status=TaskStatus.DONE, result={"all_passed": True}),
    )

    # 10. 验证最终状态
    tasks = await task_coordinator.list_tasks()
    assert all(t.status == TaskStatus.DONE for t in tasks)

    # 11. 测试写冲突
    conflict = await conflict_resolver.detect_write_conflict(
        design_entry.entry_id, coder.agent_id, current_version=999
    )
    assert conflict is not None

    resolved = await conflict_resolver.resolve_conflict(conflict.conflict_id)
    assert resolved.status.value in ("resolved", "escalated")


@pytest.mark.asyncio
async def test_multi_agent_information_sharing(
    agent_registry, blackboard, populated_agents
):
    """测试多智能体信息共享"""
    agents = populated_agents

    # 每个智能体发布信息
    topics = ["design", "code", "search_results", "nlp_analysis"]
    for i, agent in enumerate(agents):
        entry = await blackboard.create_entry(
            agent.agent_id,
            EntryCreate(
                topic=topics[i],
                content={"agent": agent.name, "data": f"info_{i}"},
            ),
        )
        assert entry.author_id == agent.agent_id

    # 验证所有智能体都能看到所有条目
    all_entries = await blackboard.list_entries()
    assert len(all_entries) == len(agents)

    # 验证按主题过滤
    for topic in topics:
        entries = await blackboard.list_entries(topic=topic)
        assert len(entries) == 1
        assert entries[0].topic == topic


@pytest.mark.asyncio
async def test_rule_engine_integration(
    agent_registry, task_coordinator, rule_engine, populated_agents
):
    """测试规则引擎与任务协调的集成"""
    from app.models import RuleCreate, RuleAction
    creator = populated_agents[0]

    # 创建规则并加载
    await rule_engine.create_rule(
        RuleCreate(
            name="Critical auto-assign",
            trigger=RuleTrigger.ON_TASK_CREATED,
            condition={"field": "priority", "operator": "eq", "value": "critical"},
            action=RuleAction.AUTO_ASSIGN,
            priority=10,
        )
    )
    await rule_engine._reload_cache()

    # 创建高优先级任务
    task = await task_coordinator.create_task(
        TaskCreate(
            title="Urgent task",
            required_capabilities=["python"],
            priority=Priority.CRITICAL,
            creator_id=creator.agent_id,
        )
    )

    # 评估规则
    results = await rule_engine.evaluate_rules(
        RuleTrigger.ON_TASK_CREATED,
        {
            "task_id": task.task_id,
            "priority": "critical",
            "required_capabilities": ["python"],
        },
    )

    # 应该有规则匹配并执行自动分配
    assert len(results) >= 1
