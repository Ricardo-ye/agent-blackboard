"""冲突解决器 - 检测并解决多智能体协作中的冲突"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.models import (
    Conflict, ConflictType, ConflictStatus, Priority,
    CONFLICT_TERMINAL_STATUSES, CONFLICT_ACTIVE_STATUSES,
)
from app.storage import StorageBackend
from app.event_bus import event_bus

if TYPE_CHECKING:
    from app.blackboard import BlackboardCore
    from app.agent_registry import AgentRegistry


class ConflictResolver:
    """冲突解决器 - 检测、记录并解决协作冲突"""

    # 按 topic 串行化「查重 + 新建」临界区，避免并发写入时各请求
    # 都查不到既有冲突而重复新建（TOCTOU）。锁粒度按 topic 细分，
    # 不同主题之间不互相阻塞。
    _opinion_locks: dict[str, asyncio.Lock] = {}

    def __init__(
        self,
        storage: StorageBackend,
        blackboard: "BlackboardCore",
        agent_registry: "AgentRegistry",
    ):
        self.storage = storage
        self.blackboard = blackboard
        self.agent_registry = agent_registry

    @classmethod
    def _lock_for(cls, topic: str) -> asyncio.Lock:
        """获取（并按需创建）某主题的互斥锁"""
        lock = cls._opinion_locks.get(topic)
        if lock is None:
            lock = cls._opinion_locks.setdefault(topic, asyncio.Lock())
        return lock

    async def detect_write_conflict(
        self,
        entry_id: str,
        agent_id: str,
        current_version: int,
    ) -> Conflict | None:
        """
        检测写冲突 - 当智能体尝试更新条目但版本不匹配时

        返回冲突记录（由调用者决定是否解决）
        """
        entry = await self.storage.get_entry(entry_id)
        if not entry:
            return None

        conflict = Conflict(
            conflict_id=str(uuid.uuid4()),
            conflict_type=ConflictType.WRITE_CONFLICT,
            status=ConflictStatus.DETECTED,
            description=(
                f"Write conflict on entry '{entry_id}': "
                f"agent {agent_id} tried to update version {current_version}, "
                f"but current version is {entry.version}"
            ),
            context={
                "entry_id": entry_id,
                "agent_id": agent_id,
                "expected_version": current_version,
                "current_version": entry.version,
                "topic": entry.topic,
            },
            involved_agents=[agent_id, entry.author_id],
        )
        await self.storage.save_conflict(conflict)
        await event_bus.publish("conflict.detected", {
            "conflict_id": conflict.conflict_id,
            "conflict_type": conflict.conflict_type.value,
            "description": conflict.description,
        })
        return conflict

    async def detect_opinion_conflict(
        self,
        topic: str,
    ) -> Conflict | None:
        """
        检测意见冲突 - 同一主题下存在相互矛盾的知识条目

        判定策略：同一主题下存在多个不同作者、且内容不一致的条目。

        幂等保证：同一 topic 下若已存在未终结（DETECTED / RESOLVING）的意见冲突，
        则复用该记录并刷新条目集合，不再新建 —— 否则每写入一条条目都会
        重复检出一次全主题冲突，导致冲突表膨胀（原实现的缺陷）。

        并发保证：「查重 + 新建」整段置于按 topic 的互斥锁内，
        避免并发写入时所有请求都查不到既有冲突而各自新建（TOCTOU）。
        """
        async with self._lock_for(topic):
            return await self._detect_opinion_conflict_locked(topic)

    async def _detect_opinion_conflict_locked(
        self,
        topic: str,
    ) -> Conflict | None:
        """意见冲突检测的实际执行体（调用方须已持有 topic 锁）"""
        entries = await self.storage.list_entries(topic)
        if len(entries) < 2:
            return None

        # 检查是否有不同作者的条目
        authors = set(e.author_id for e in entries)
        if len(authors) < 2:
            return None

        # 检查内容是否有显著差异（简化：直接比较内容字符串）
        contents = [str(e.content) for e in entries]
        if len(set(contents)) < 2:
            return None

        entry_ids = [e.entry_id for e in entries]
        author_list = list(authors)

        # 幂等第一步：复用该 topic 下仍在活跃态的同类冲突。
        # 注意必须包含 ESCALATED —— 升级待人工并不代表问题消失，
        # 漏掉它会导致每次新写入都新建一条冲突并再次升级，形成雪崩。
        open_conflicts = []
        for st in CONFLICT_ACTIVE_STATUSES:
            open_conflicts += await self.storage.list_conflicts(st)
        for existing in open_conflicts:
            if (
                existing.conflict_type == ConflictType.OPINION_CONFLICT
                and existing.context.get("topic") == topic
            ):
                # 条目集合可能已变化，刷新上下文但不新建记录
                if (
                    existing.context.get("entry_ids") != entry_ids
                    or existing.involved_agents != author_list
                ):
                    existing.context["entry_ids"] = entry_ids
                    existing.context["authors"] = author_list
                    existing.involved_agents = author_list
                    existing.description = (
                        f"Potential opinion conflict on topic '{topic}' "
                        f"with {len(entries)} entries"
                    )
                    await self.storage.save_conflict(existing)
                return existing

        # 幂等第二步：若该 topic 已有历史冲突（含已解决）且条目集合未变化，
        # 说明分歧没有新进展，不再重复新建记录 —— 避免 conflict 表被噪音填满。
        history = await self.storage.list_conflicts()
        for past in history:
            if (
                past.conflict_type == ConflictType.OPINION_CONFLICT
                and past.context.get("topic") == topic
                and past.context.get("entry_ids") == entry_ids
            ):
                return past

        conflict = Conflict(
            conflict_id=str(uuid.uuid4()),
            conflict_type=ConflictType.OPINION_CONFLICT,
            status=ConflictStatus.DETECTED,
            description=f"Potential opinion conflict on topic '{topic}' with {len(entries)} entries",
            context={
                "topic": topic,
                "entry_ids": entry_ids,
                "authors": author_list,
            },
            involved_agents=author_list,
        )
        await self.storage.save_conflict(conflict)
        await event_bus.publish("conflict.detected", {
            "conflict_id": conflict.conflict_id,
            "conflict_type": conflict.conflict_type.value,
            "description": conflict.description,
        })
        return conflict

    async def detect_task_assignment_conflict(
        self,
        agent_id: str,
        task_id: str,
    ) -> Conflict | None:
        """
        检测任务分配冲突 - 智能体当前负载过高时
        """
        agent = await self.storage.get_agent(agent_id)
        if not agent:
            return None

        # 负载阈值：当前任务数 >= 3 视为过载
        if agent.current_task_count < 3:
            return None

        conflict = Conflict(
            conflict_id=str(uuid.uuid4()),
            conflict_type=ConflictType.TASK_ASSIGNMENT,
            status=ConflictStatus.DETECTED,
            description=(
                f"Agent {agent_id} is overloaded with {agent.current_task_count} tasks, "
                f"trying to assign task {task_id}"
            ),
            context={
                "agent_id": agent_id,
                "task_id": task_id,
                "current_load": agent.current_task_count,
            },
            involved_agents=[agent_id],
        )
        await self.storage.save_conflict(conflict)
        await event_bus.publish("conflict.detected", {
            "conflict_id": conflict.conflict_id,
            "conflict_type": conflict.conflict_type.value,
            "description": conflict.description,
        })
        return conflict

    # 冲突终态：进入这些状态后不允许再次解决（统一定义见 app.models）
    TERMINAL_STATUSES = CONFLICT_TERMINAL_STATUSES

    async def resolve_conflict(
        self,
        conflict_id: str,
        strategy: str | None = None,
        resolved_by: str = "system",
    ) -> Conflict:
        """
        解决冲突 - 按策略链尝试解决

        策略链：
        1. 版本合并 (merge) - 适用于写冲突
        2. 优先级仲裁 (priority) - 适用于任务/资源冲突
        3. 置信度投票 (vote) - 适用于意见冲突
        4. 升级人工 (escalate) - 最终手段

        幂等保护：已处于终态（RESOLVED / DISMISSED）的冲突拒绝重复解决，
        避免 vote 策略每次重建 merged 条目造成脏数据累积。
        """
        conflict = await self.storage.get_conflict(conflict_id)
        if not conflict:
            raise ValueError(f"Conflict {conflict_id} not found")

        if conflict.status in self.TERMINAL_STATUSES:
            raise ValueError(
                f"Conflict {conflict_id} is already {conflict.status.value} "
                f"and cannot be resolved again"
            )

        conflict.status = ConflictStatus.RESOLVING
        await self.storage.save_conflict(conflict)

        if strategy is None:
            strategy = await self._select_strategy(conflict)

        resolution = await self._apply_strategy(conflict, strategy, resolved_by)

        # escalate策略会在_apply_strategy中设置状态为ESCALATED，不覆盖
        if strategy != "escalate":
            conflict.status = ConflictStatus.RESOLVED
        conflict.resolution = resolution
        conflict.resolved_by = resolved_by
        conflict.resolved_at = datetime.now(timezone.utc)
        await self.storage.save_conflict(conflict)

        await event_bus.publish("conflict.resolved", {
            "conflict_id": conflict_id,
            "conflict_type": conflict.conflict_type.value,
            "strategy": strategy,
            "resolution": resolution,
        })
        return conflict

    async def _select_strategy(self, conflict: Conflict) -> str:
        """根据冲突类型选择解决策略"""
        strategy_map = {
            ConflictType.WRITE_CONFLICT: "merge",
            ConflictType.TASK_ASSIGNMENT: "priority",
            ConflictType.RESOURCE_CONFLICT: "priority",
            ConflictType.OPINION_CONFLICT: "vote",
        }
        return strategy_map.get(conflict.conflict_type, "escalate")

    async def _apply_strategy(
        self,
        conflict: Conflict,
        strategy: str,
        resolved_by: str,
    ) -> str:
        """应用指定解决策略，返回解决描述"""
        if strategy == "merge":
            return await self._resolve_by_merge(conflict, resolved_by)
        elif strategy == "priority":
            return await self._resolve_by_priority(conflict, resolved_by)
        elif strategy == "vote":
            return await self._resolve_by_vote(conflict, resolved_by)
        else:
            return await self._resolve_by_escalate(conflict, resolved_by)

    async def _resolve_by_merge(self, conflict: Conflict, resolved_by: str) -> str:
        """合并解决 - 重新获取最新版本，让冲突方基于新版本重试"""
        entry_id = conflict.context.get("entry_id")
        if entry_id:
            entry = await self.storage.get_entry(entry_id)
            if entry:
                return (
                    f"Merge resolution: refreshed to version {entry.version}. "
                    f"Agent should re-apply changes on top of current version."
                )
        return "Merge resolution applied: no action needed."

    async def _resolve_by_priority(self, conflict: Conflict, resolved_by: str) -> str:
        """优先级仲裁 - 高优先级任务/资源优先"""
        if conflict.conflict_type == ConflictType.TASK_ASSIGNMENT:
            task_id = conflict.context.get("task_id")
            task = (
                await self.storage.get_task(task_id)
                if isinstance(task_id, str)
                else None
            )
            if task:
                return (
                    f"Priority arbitration: task '{task.title}' "
                    f"(priority: {task.priority.value}) queued for next available agent."
                )
        return "Priority arbitration applied."

    async def _resolve_by_vote(self, conflict: Conflict, resolved_by: str) -> str:
        """置信度加权投票解决意见冲突"""
        topic = conflict.context.get("topic")
        entry_ids = conflict.context.get("entry_ids", [])
        if topic and entry_ids:
            entries = []
            for eid in entry_ids:
                entry = await self.storage.get_entry(eid)
                if entry:
                    entries.append(entry)

            if entries:
                # 找出置信度最高的条目作为胜出者
                winner = max(entries, key=lambda e: e.confidence)
                # 合并为新条目
                merged = await self.blackboard.merge_entries(
                    entry_ids, topic, resolved_by
                )
                return (
                    f"Vote resolution: entry {winner.entry_id} won with "
                    f"confidence {winner.confidence:.2f}. Merged into {merged.entry_id}."
                )
        return "Vote resolution applied."

    async def _resolve_by_escalate(self, conflict: Conflict, resolved_by: str) -> str:
        """升级人工处理"""
        conflict.status = ConflictStatus.ESCALATED
        await self.storage.save_conflict(conflict)
        await event_bus.publish("conflict.escalated", {
            "conflict_id": conflict.conflict_id,
            "description": conflict.description,
        })
        return "Conflict escalated for human review."

    async def get_conflict(self, conflict_id: str) -> Conflict | None:
        return await self.storage.get_conflict(conflict_id)

    async def list_conflicts(
        self, status: ConflictStatus | None = None
    ) -> list[Conflict]:
        return await self.storage.list_conflicts(status)
