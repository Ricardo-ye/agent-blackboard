"""测试配置与夹具"""
import sys
import os
import pytest
import pytest_asyncio

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.storage import SQLiteStorage
from app.agent_registry import AgentRegistry
from app.blackboard import BlackboardCore
from app.task_coordinator import TaskCoordinator
from app.conflict_resolver import ConflictResolver
from app.rule_engine import RuleEngine
from app.services import ServiceContainer


@pytest_asyncio.fixture
async def storage(tmp_path):
    """临时数据库存储"""
    db_path = str(tmp_path / "test.db")
    storage = SQLiteStorage(db_path)
    await storage.initialize()
    yield storage
    await storage.close()


@pytest_asyncio.fixture
async def agent_registry(storage):
    return AgentRegistry(storage)


@pytest_asyncio.fixture
async def blackboard(storage):
    return BlackboardCore(storage)


@pytest_asyncio.fixture
async def task_coordinator(storage, agent_registry):
    return TaskCoordinator(storage, agent_registry)


@pytest_asyncio.fixture
async def conflict_resolver(storage, blackboard, agent_registry):
    return ConflictResolver(storage, blackboard, agent_registry)


@pytest_asyncio.fixture
async def rule_engine(storage, task_coordinator, conflict_resolver):
    engine = RuleEngine(storage, task_coordinator, conflict_resolver)
    await engine.initialize()
    return engine


@pytest_asyncio.fixture
async def populated_agents(agent_registry):
    """注册一组测试智能体"""
    agents = []
    configs = [
        ("CodeAgent", ["code_gen", "python", "fastapi"]),
        ("TestAgent", ["testing", "python"]),
        ("SearchAgent", ["search", "web_scraping"]),
        ("NLUAgent", ["nlp", "text_analysis"]),
    ]
    for name, caps in configs:
        from app.models import AgentCreate
        agent = await agent_registry.register(AgentCreate(name=name, capabilities=caps))
        agents.append(agent)
    return agents
