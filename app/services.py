"""服务聚合层 - 统一管理所有核心服务与生命周期"""
from __future__ import annotations

from app.storage import SQLiteStorage
from app.agent_registry import AgentRegistry
from app.blackboard import BlackboardCore
from app.task_coordinator import TaskCoordinator
from app.conflict_resolver import ConflictResolver
from app.rule_engine import RuleEngine
from app.event_bus import event_bus
from app.models import RuleTrigger


class ServiceContainer:
    """服务容器 - 管理所有核心服务的初始化与依赖注入"""

    def __init__(self):
        self.storage = SQLiteStorage()
        self.agent_registry = AgentRegistry(self.storage)
        self.blackboard = BlackboardCore(self.storage)
        self.task_coordinator = TaskCoordinator(self.storage, self.agent_registry)
        self.conflict_resolver = ConflictResolver(
            self.storage, self.blackboard, self.agent_registry
        )
        self.rule_engine = RuleEngine(
            self.storage, self.task_coordinator, self.conflict_resolver
        )

    async def initialize(self) -> None:
        """初始化所有服务"""
        await self.storage.initialize()
        await self.rule_engine.initialize()
        await self.rule_engine.seed_default_rules()
        await self.agent_registry.start_heartbeat_monitor()
        await self._register_event_rules()

    async def shutdown(self) -> None:
        """关闭所有服务"""
        await self.agent_registry.stop_heartbeat_monitor()
        await self.storage.close()

    async def _register_event_rules(self) -> None:
        """注册事件到规则引擎的自动评估"""
        # 当任务创建时，评估ON_TASK_CREATED规则
        async def on_task_created(event):
            await self.rule_engine.evaluate_rules(
                RuleTrigger.ON_TASK_CREATED, event.data
            )

        # 当冲突检测时，评估ON_CONFLICT_DETECTED规则
        async def on_conflict_detected(event):
            await self.rule_engine.evaluate_rules(
                RuleTrigger.ON_CONFLICT_DETECTED, event.data
            )

        # 当智能体注册时，评估ON_AGENT_REGISTERED规则
        async def on_agent_registered(event):
            await self.rule_engine.evaluate_rules(
                RuleTrigger.ON_AGENT_REGISTERED, event.data
            )

        # 当条目创建时，评估ON_ENTRY_CREATED规则
        async def on_entry_created(event):
            # 检测同主题意见冲突
            topic = event.data.get("topic")
            if topic and event.data.get("check_conflicts", True):
                await self.conflict_resolver.detect_opinion_conflict(topic)
            await self.rule_engine.evaluate_rules(
                RuleTrigger.ON_ENTRY_CREATED, event.data
            )

        event_bus.on("task.created", on_task_created)
        event_bus.on("conflict.detected", on_conflict_detected)
        event_bus.on("agent.registered", on_agent_registered)
        event_bus.on("entry.created", on_entry_created)


# 全局服务容器实例
services = ServiceContainer()
