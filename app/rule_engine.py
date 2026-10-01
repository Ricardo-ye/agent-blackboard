"""协作规则引擎 - 动态规则配置与执行"""
from __future__ import annotations

import operator
import uuid
from typing import Any, Callable, TYPE_CHECKING

from pydantic import ValidationError

from app.models import (
    CollaborationRule, RuleCreate, RuleTrigger, RuleAction, Event,
    TaskUpdate, TaskStatus, Priority,
)
from app.storage import StorageBackend
from app.event_bus import event_bus

if TYPE_CHECKING:
    from app.task_coordinator import TaskCoordinator
    from app.conflict_resolver import ConflictResolver


class RuleEngine:
    """
    协作规则引擎 - 支持热加载动态规则

    规则匹配条件支持简单的键值比较操作符：
    - eq, ne: 等于/不等于
    - gt, lt: 大于/小于
    - gte, lte: 大于等于/小于等于
    - in, nin: 在列表中/不在列表中
    - contains: 包含
    """

    SUPPORTED_OPERATORS = {
        "eq": operator.eq,
        "ne": operator.ne,
        "gt": operator.gt,
        "lt": operator.lt,
        "gte": operator.ge,
        "lte": operator.le,
        "in": lambda a, b: b in a if isinstance(a, (list, str)) else False,
        "nin": lambda a, b: b not in a if isinstance(a, (list, str)) else False,
        "contains": lambda a, b: b in a if isinstance(a, (list, str)) else False,
    }

    # 允许被更新的字段白名单
    # 注意：rule_id / trigger / action 是规则的语义骨架，一经创建不允许变更，
    # 否则会破坏缓存分组（_rule_cache 按 trigger 分组）并可能导致脏数据。
    ALLOWED_UPDATE_FIELDS = frozenset({
        "name", "condition", "action_params", "enabled", "priority",
    })

    def __init__(
        self,
        storage: StorageBackend,
        task_coordinator: "TaskCoordinator | None" = None,
        conflict_resolver: "ConflictResolver | None" = None,
    ):
        self.storage = storage
        self.task_coordinator = task_coordinator
        self.conflict_resolver = conflict_resolver
        # 缓存已编译的规则，避免每次事件都查询数据库
        self._rule_cache: dict[RuleTrigger, list[CollaborationRule]] = {}
        self._condition_cache: dict[
            str, Callable[[dict[str, Any]], bool]
        ] = {}
        self._cache_loaded = False

    async def initialize(self) -> None:
        """初始化规则引擎，加载规则缓存并注册事件处理器"""
        await self._reload_cache()
        # 注册规则变更事件处理器
        event_bus.on("rule.created", self._on_rule_changed)
        event_bus.on("rule.updated", self._on_rule_changed)
        event_bus.on("rule.deleted", self._on_rule_changed)

    async def _on_rule_changed(self, event: Event) -> None:
        """规则变更时重新加载缓存"""
        await self._reload_cache()

    async def _reload_cache(self) -> None:
        """从存储重新加载所有规则到缓存"""
        rules = await self.storage.list_rules()
        cache: dict[RuleTrigger, list[CollaborationRule]] = {}
        condition_cache: dict[str, Callable[[dict[str, Any]], bool]] = {}
        for rule in rules:
            if not rule.enabled:
                continue
            cache.setdefault(rule.trigger, []).append(rule)
            condition_cache[rule.rule_id] = self._compile_condition(rule.condition)
        # 每个触发类型内按priority降序排序
        for trigger in cache:
            cache[trigger].sort(key=lambda r: r.priority, reverse=True)
        self._rule_cache = cache
        self._condition_cache = condition_cache
        self._cache_loaded = True

    async def create_rule(self, rule_create: RuleCreate) -> CollaborationRule:
        """创建协作规则"""
        rule = CollaborationRule(
            rule_id=str(uuid.uuid4()),
            name=rule_create.name,
            trigger=rule_create.trigger,
            condition=rule_create.condition,
            action=rule_create.action,
            action_params=rule_create.action_params,
            enabled=rule_create.enabled,
            priority=rule_create.priority,
        )
        await self.storage.save_rule(rule)
        await event_bus.publish("rule.created", {
            "rule_id": rule.rule_id,
            "name": rule.name,
            "trigger": rule.trigger.value,
        })
        return rule

    async def update_rule(
        self,
        rule_id: str,
        updates: dict[str, Any],
    ) -> CollaborationRule:
        """
        更新规则

        安全约束：
        - 仅允许 ALLOWED_UPDATE_FIELDS 中的字段被修改（黑名单字段如 rule_id/trigger/action 一律拒绝）
        - 合并后通过 Pydantic 重新校验，类型错误在此抛出 ValidationError，不落库
        """
        rule = await self.storage.get_rule(rule_id)
        if not rule:
            raise ValueError(f"Rule {rule_id} not found")

        if not isinstance(updates, dict):
            raise ValueError("updates must be a mapping of field -> value")

        # 1) 字段白名单校验：拒绝未授权字段（含主键注入）
        illegal = sorted(set(updates) - self.ALLOWED_UPDATE_FIELDS)
        if illegal:
            raise ValueError(
                f"Fields not allowed to update: {illegal}. "
                f"Allowed: {sorted(self.ALLOWED_UPDATE_FIELDS)}"
            )

        # 2) 全量合并后走 Pydantic 校验，避免非法类型写入数据库
        merged = rule.model_dump()
        merged.update(updates)
        try:
            validated = CollaborationRule(**merged)
        except ValidationError as e:
            raise ValueError(f"Invalid rule payload: {e}") from e

        await self.storage.save_rule(validated)
        await event_bus.publish("rule.updated", {
            "rule_id": validated.rule_id,
            "name": validated.name,
        })
        return validated

    async def delete_rule(self, rule_id: str) -> bool:
        """删除规则"""
        rule = await self.storage.get_rule(rule_id)
        if not rule:
            return False
        await self.storage.delete_rule(rule_id)
        await event_bus.publish("rule.deleted", {"rule_id": rule_id})
        return True

    async def get_rule(self, rule_id: str) -> CollaborationRule | None:
        return await self.storage.get_rule(rule_id)

    async def list_rules(self) -> list[CollaborationRule]:
        return await self.storage.list_rules()

    async def evaluate_rules(
        self,
        trigger: RuleTrigger,
        event_data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        评估并执行匹配的规则

        按priority排序执行，返回所有匹配规则的执行结果
        """
        if not self._cache_loaded:
            await self._reload_cache()

        rules = self._rule_cache.get(trigger, [])
        results = []

        for rule in rules:
            matcher = self._condition_cache.get(rule.rule_id)
            if matcher is None:
                matcher = self._compile_condition(rule.condition)
                self._condition_cache[rule.rule_id] = matcher
            if matcher(event_data):
                result = await self._execute_action(rule, event_data)
                results.append({
                    "rule_id": rule.rule_id,
                    "rule_name": rule.name,
                    "action": rule.action.value,
                    "result": result,
                })

        return results

    def _match_condition(
        self,
        condition: dict[str, Any],
        event_data: dict[str, Any],
    ) -> bool:
        """
        匹配条件 - 支持嵌套条件

        condition格式：
        {
            "field": "priority",
            "operator": "eq",
            "value": "high"
        }

        或复合条件：
        {
            "and": [
                {"field": "priority", "operator": "eq", "value": "high"},
                {"field": "capabilities", "operator": "contains", "value": "nlp"}
            ]
        }
        """
        return self._compile_condition(condition)(event_data)

    def _compile_condition(
        self,
        condition: dict[str, Any],
    ) -> Callable[[dict[str, Any]], bool]:
        """将条件树编译为可重用的匹配器。

        规则热加载时完成路径分割、操作符查找和复合树构建，事件
        热路径只读取字段并执行比较。无效结构保持原有的「不匹配」语义。
        """
        if not isinstance(condition, dict):
            return lambda _event_data: False
        if not condition:
            return lambda _event_data: True

        if "and" in condition:
            children = condition.get("and")
            if not isinstance(children, list):
                return lambda _event_data: False
            matchers = tuple(self._compile_condition(child) for child in children)
            return lambda event_data: all(matcher(event_data) for matcher in matchers)

        if "or" in condition:
            children = condition.get("or")
            if not isinstance(children, list):
                return lambda _event_data: False
            matchers = tuple(self._compile_condition(child) for child in children)
            return lambda event_data: any(matcher(event_data) for matcher in matchers)

        field = condition.get("field")
        if not field or not isinstance(field, str):
            return lambda _event_data: True

        handler = self.SUPPORTED_OPERATORS.get(condition.get("operator", "eq"))
        if handler is None:
            return lambda _event_data: False

        path = tuple(field.split("."))
        expected = condition.get("value")

        def match(event_data: dict[str, Any]) -> bool:
            actual: Any = event_data
            for part in path:
                if not isinstance(actual, dict):
                    return False
                actual = actual.get(part)
            if actual is None:
                return False
            try:
                return bool(handler(actual, expected))
            except (TypeError, ValueError):
                return False

        return match

    async def _execute_action(
        self,
        rule: CollaborationRule,
        event_data: dict[str, Any],
    ) -> str:
        """执行规则动作"""
        action = rule.action
        params = rule.action_params

        if action == RuleAction.AUTO_ASSIGN and self.task_coordinator:
            task_id = params.get("task_id") or event_data.get("task_id")
            if task_id:
                try:
                    task = await self.task_coordinator.auto_assign(task_id)
                    if task:
                        return f"Auto-assigned task {task_id} to {task.assignee_id}"
                    return f"No available agent for task {task_id}"
                except Exception as e:
                    return f"Auto-assign failed: {e}"

        elif action == RuleAction.ESCALATE and self.conflict_resolver:
            conflict_id = params.get("conflict_id") or event_data.get("conflict_id")
            if conflict_id:
                try:
                    await self.conflict_resolver.resolve_conflict(
                        conflict_id, strategy="escalate"
                    )
                    return f"Escalated conflict {conflict_id}"
                except Exception as e:
                    return f"Escalate failed: {e}"

        elif action == RuleAction.NOTIFY:
            channel = params.get("channel", "system")
            message = params.get("message", "Rule triggered")
            await event_bus.publish("notification", {
                "channel": channel,
                "message": message,
                "rule_id": rule.rule_id,
                "event_data": event_data,
            })
            return f"Notification sent to {channel}"

        elif action == RuleAction.SET_PRIORITY:
            # 设置任务优先级
            task_id = event_data.get("task_id")
            priority = params.get("priority")
            if task_id and priority and self.task_coordinator:
                try:
                    await self.task_coordinator.update_task(
                        task_id, "rule_engine",
                        TaskUpdate(priority=Priority(priority))
                    )
                    return f"Set task {task_id} priority to {priority}"
                except Exception as e:
                    return f"Set priority failed: {e}"

        elif action == RuleAction.BLOCK_TASK:
            task_id = params.get("task_id") or event_data.get("task_id")
            if task_id and self.task_coordinator:
                try:
                    await self.task_coordinator.update_task(
                        task_id, "rule_engine",
                        TaskUpdate(status=TaskStatus.BLOCKED)
                    )
                    return f"Blocked task {task_id}"
                except Exception as e:
                    return f"Block task failed: {e}"

        return f"Action {action.value} executed (no-op or unsupported context)"

    async def seed_default_rules(self) -> None:
        """种子默认规则 - 系统初始化时调用"""
        existing = await self.storage.list_rules()
        if existing:
            return  # 已有规则则不重复创建

        default_rules = [
            RuleCreate(
                name="高优先级任务自动分配",
                trigger=RuleTrigger.ON_TASK_CREATED,
                condition={"field": "priority", "operator": "eq", "value": "high"},
                action=RuleAction.AUTO_ASSIGN,
                priority=10,
            ),
            RuleCreate(
                name="严重任务自动分配",
                trigger=RuleTrigger.ON_TASK_CREATED,
                condition={"field": "priority", "operator": "eq", "value": "critical"},
                action=RuleAction.AUTO_ASSIGN,
                priority=20,
            ),
            RuleCreate(
                name="冲突自动升级",
                trigger=RuleTrigger.ON_CONFLICT_DETECTED,
                condition={},
                action=RuleAction.ESCALATE,
                priority=5,
            ),
            RuleCreate(
                name="新智能体注册通知",
                trigger=RuleTrigger.ON_AGENT_REGISTERED,
                condition={},
                action=RuleAction.NOTIFY,
                action_params={"channel": "system", "message": "New agent registered"},
                priority=1,
            ),
        ]

        for rule_create in default_rules:
            await self.create_rule(rule_create)

        await self._reload_cache()
