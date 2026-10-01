"""智能体注册与管理测试"""
import pytest
from app.models import AgentCreate, AgentStatus


@pytest.mark.asyncio
async def test_register_agent(agent_registry):
    """测试智能体注册"""
    agent = await agent_registry.register(
        AgentCreate(name="TestBot", capabilities=["nlp", "search"])
    )
    assert agent.agent_id is not None
    assert agent.name == "TestBot"
    assert agent.capabilities == ["nlp", "search"]
    assert agent.status == AgentStatus.ONLINE


@pytest.mark.asyncio
async def test_unregister_agent(agent_registry):
    """测试智能体注销"""
    agent = await agent_registry.register(AgentCreate(name="ToDelete"))
    success = await agent_registry.unregister(agent.agent_id)
    assert success is True
    found = await agent_registry.get(agent.agent_id)
    assert found is None


@pytest.mark.asyncio
async def test_heartbeat(agent_registry):
    """测试心跳更新"""
    agent = await agent_registry.register(AgentCreate(name="HeartbeatBot"))
    old_heartbeat = agent.last_heartbeat

    # 手动修改为离线
    agent.status = AgentStatus.OFFLINE
    await agent_registry.storage.save_agent(agent)

    # 心跳应该恢复在线
    updated = await agent_registry.heartbeat(agent.agent_id)
    assert updated is not None
    assert updated.status == AgentStatus.ONLINE
    assert updated.last_heartbeat >= old_heartbeat


@pytest.mark.asyncio
async def test_find_candidates(agent_registry, populated_agents):
    """测试能力匹配候选查找"""
    # 查找python能力的候选
    candidates = await agent_registry.find_candidates(["python"])
    assert len(candidates) >= 2  # CodeAgent and TestAgent both have python
    # 两个智能体对 ["python"] 的匹配度相同（各命中 1 项能力、均在线、负载为 0），
    # 因此这里只断言「都进入候选」，不断言具体先后（同分顺序由 agent_id 稳定决定）。
    names = {a.name for a in candidates}
    assert {"CodeAgent", "TestAgent"} <= names

    # 查找不存在的能力
    no_candidates = await agent_registry.find_candidates(["quantum_computing"])
    assert no_candidates == []


@pytest.mark.asyncio
async def test_find_candidates_prefers_more_matches(agent_registry):
    """匹配能力更多的智能体应排在前面"""
    await agent_registry.register(
        AgentCreate(name="Weak", capabilities=["python"])
    )
    strong = await agent_registry.register(
        AgentCreate(name="Strong", capabilities=["python", "code_gen", "fastapi"])
    )

    candidates = await agent_registry.find_candidates(["python", "code_gen", "fastapi"])
    assert candidates[0].agent_id == strong.agent_id


@pytest.mark.asyncio
async def test_find_candidates_prefers_light_load(agent_registry):
    """能力相同时，负载更低的智能体应排在前面"""
    loaded = await agent_registry.register(
        AgentCreate(name="Loaded", capabilities=["python"])
    )
    idle = await agent_registry.register(
        AgentCreate(name="Idle", capabilities=["python"])
    )
    for _ in range(4):
        await agent_registry.increment_task_count(loaded.agent_id)

    candidates = await agent_registry.find_candidates(["python"])
    assert candidates[0].agent_id == idle.agent_id


@pytest.mark.asyncio
async def test_task_count_management(agent_registry):
    """测试任务计数管理"""
    agent = await agent_registry.register(AgentCreate(name="LoadBot"))
    assert agent.current_task_count == 0

    await agent_registry.increment_task_count(agent.agent_id)
    await agent_registry.increment_task_count(agent.agent_id)
    updated = await agent_registry.get(agent.agent_id)
    assert updated.current_task_count == 2

    await agent_registry.decrement_task_count(agent.agent_id)
    updated = await agent_registry.get(agent.agent_id)
    assert updated.current_task_count == 1

    # 不应减到负数
    await agent_registry.decrement_task_count(agent.agent_id)
    await agent_registry.decrement_task_count(agent.agent_id)
    updated = await agent_registry.get(agent.agent_id)
    assert updated.current_task_count == 0
