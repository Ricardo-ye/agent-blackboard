"""规则引擎测试"""
import pytest
from app.models import (
    RuleCreate, RuleTrigger, RuleAction, AgentCreate,
    TaskCreate, Priority,
)


@pytest.mark.asyncio
async def test_create_rule(rule_engine):
    """测试规则创建"""
    rule = await rule_engine.create_rule(
        RuleCreate(
            name="Test Rule",
            trigger=RuleTrigger.ON_TASK_CREATED,
            condition={"field": "priority", "operator": "eq", "value": "high"},
            action=RuleAction.AUTO_ASSIGN,
            priority=10,
        )
    )
    assert rule.rule_id is not None
    assert rule.name == "Test Rule"
    assert rule.enabled is True


@pytest.mark.asyncio
async def test_match_condition_simple(rule_engine):
    """测试简单条件匹配"""
    # 匹配
    assert rule_engine._match_condition(
        {"field": "priority", "operator": "eq", "value": "high"},
        {"priority": "high"},
    ) is True

    # 不匹配
    assert rule_engine._match_condition(
        {"field": "priority", "operator": "eq", "value": "high"},
        {"priority": "low"},
    ) is False

    # 无条件匹配
    assert rule_engine._match_condition({}, {"any": "data"}) is True


@pytest.mark.asyncio
async def test_match_condition_operators(rule_engine):
    """测试各种操作符"""
    assert rule_engine._match_condition(
        {"field": "count", "operator": "gt", "value": 5}, {"count": 10}
    ) is True
    assert rule_engine._match_condition(
        {"field": "count", "operator": "lt", "value": 5}, {"count": 10}
    ) is False
    assert rule_engine._match_condition(
        {"field": "name", "operator": "contains", "value": "test"}, {"name": "my_test"}
    ) is True
    assert rule_engine._match_condition(
        {"field": "tags", "operator": "in", "value": "python"}, {"tags": ["python", "js"]}
    ) is True


@pytest.mark.asyncio
async def test_match_condition_and_or(rule_engine):
    """测试复合条件"""
    and_cond = {
        "and": [
            {"field": "priority", "operator": "eq", "value": "high"},
            {"field": "urgent", "operator": "eq", "value": True},
        ]
    }
    assert rule_engine._match_condition(and_cond, {"priority": "high", "urgent": True}) is True
    assert rule_engine._match_condition(and_cond, {"priority": "high", "urgent": False}) is False

    or_cond = {
        "or": [
            {"field": "priority", "operator": "eq", "value": "high"},
            {"field": "priority", "operator": "eq", "value": "critical"},
        ]
    }
    assert rule_engine._match_condition(or_cond, {"priority": "critical"}) is True
    assert rule_engine._match_condition(or_cond, {"priority": "low"}) is False


@pytest.mark.asyncio
async def test_evaluate_auto_assign_rule(
    rule_engine, agent_registry, task_coordinator
):
    """测试规则自动触发任务分配"""
    creator = await agent_registry.register(AgentCreate(name="Creator"))
    agent = await agent_registry.register(
        AgentCreate(name="CodeAgent", capabilities=["python", "code_gen"])
    )

    await rule_engine.create_rule(
        RuleCreate(
            name="Auto assign python tasks",
            trigger=RuleTrigger.ON_TASK_CREATED,
            condition={"field": "priority", "operator": "eq", "value": "normal"},
            action=RuleAction.AUTO_ASSIGN,
            priority=5,
        )
    )
    await rule_engine._reload_cache()

    task = await task_coordinator.create_task(
        TaskCreate(
            title="Python Task",
            required_capabilities=["python"],
            creator_id=creator.agent_id,
        )
    )

    results = await rule_engine.evaluate_rules(
        RuleTrigger.ON_TASK_CREATED,
        {"task_id": task.task_id, "priority": "normal", "required_capabilities": ["python"]},
    )

    assert len(results) >= 1
    assert any("auto" in r["action"] for r in results)


@pytest.mark.asyncio
async def test_rule_priority_ordering(rule_engine):
    """测试规则优先级排序"""
    await rule_engine.create_rule(
        RuleCreate(
            name="Low Priority",
            trigger=RuleTrigger.ON_TASK_CREATED,
            condition={},
            action=RuleAction.NOTIFY,
            priority=1,
        )
    )
    await rule_engine.create_rule(
        RuleCreate(
            name="High Priority",
            trigger=RuleTrigger.ON_TASK_CREATED,
            condition={},
            action=RuleAction.NOTIFY,
            priority=10,
        )
    )
    await rule_engine._reload_cache()

    rules = rule_engine._rule_cache.get(RuleTrigger.ON_TASK_CREATED, [])
    assert len(rules) == 2
    assert rules[0].name == "High Priority"
    assert rules[1].name == "Low Priority"


@pytest.mark.asyncio
async def test_disabled_rule_not_evaluated(rule_engine):
    """测试禁用的规则不被评估"""
    rule = await rule_engine.create_rule(
        RuleCreate(
            name="Disabled Rule",
            trigger=RuleTrigger.ON_TASK_CREATED,
            condition={},
            action=RuleAction.NOTIFY,
            enabled=False,
        )
    )
    await rule_engine._reload_cache()

    rules = rule_engine._rule_cache.get(RuleTrigger.ON_TASK_CREATED, [])
    assert all(r.rule_id != rule.rule_id for r in rules)
