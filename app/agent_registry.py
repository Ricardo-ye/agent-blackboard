"""智能体注册与管理服务"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from app.models import Agent, AgentCreate, AgentStatus
from app.storage import StorageBackend
from app.event_bus import event_bus
from config import HEARTBEAT_TIMEOUT_SECONDS, HEARTBEAT_CHECK_INTERVAL

logger = logging.getLogger(__name__)


class AgentRegistry:
    """智能体注册中心 - 管理智能体生命周期、心跳与能力"""

    def __init__(self, storage: StorageBackend):
        self.storage = storage
        self._heartbeat_task: asyncio.Task | None = None

    async def start_heartbeat_monitor(self) -> None:
        """启动心跳监控后台任务"""
        if self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat_monitor(self) -> None:
        """停止心跳监控"""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

    async def _heartbeat_loop(self) -> None:
        """定期检查心跳超时"""
        while True:
            try:
                await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL)
                await self._check_heartbeats()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Heartbeat check error: %s", e, exc_info=True)

    async def _check_heartbeats(self) -> None:
        """
        检查所有智能体的心跳，标记超时为离线

        使用单条 UPDATE ... RETURNING 批量完成迁移，避免先加载全部
        智能体再逐行更新的 N+1 开销，同时保持心跳与超时检查的原子性。
        """
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(seconds=HEARTBEAT_TIMEOUT_SECONDS)).isoformat()
        stale_agents = await self.storage.mark_stale_agents_offline(cutoff)

        for agent in stale_agents:
            await event_bus.publish("agent.offline", {
                "agent_id": agent.agent_id,
                "name": agent.name,
                "reason": "heartbeat_timeout",
            })

    async def register(self, agent_create: AgentCreate) -> Agent:
        """注册新智能体"""
        agent = Agent(
            agent_id=str(uuid.uuid4()),
            name=agent_create.name,
            capabilities=agent_create.capabilities,
            endpoint=agent_create.endpoint,
            metadata=agent_create.metadata,
        )
        await self.storage.save_agent(agent)
        await event_bus.publish("agent.registered", {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "capabilities": agent.capabilities,
        })
        return agent

    async def unregister(self, agent_id: str) -> bool:
        """注销智能体"""
        agent = await self.storage.get_agent(agent_id)
        if not agent:
            return False
        await self.storage.delete_agent(agent_id)
        await event_bus.publish("agent.unregistered", {
            "agent_id": agent_id,
            "name": agent.name,
        })
        return True

    async def get(self, agent_id: str) -> Agent | None:
        return await self.storage.get_agent(agent_id)

    async def list_all(self) -> list[Agent]:
        return await self.storage.list_agents()

    async def heartbeat(self, agent_id: str) -> Agent | None:
        """更新智能体心跳"""
        agent = await self.storage.get_agent(agent_id)
        if not agent:
            return None
        agent.last_heartbeat = datetime.now(timezone.utc)
        if agent.status == AgentStatus.OFFLINE:
            agent.status = AgentStatus.ONLINE
        await self.storage.save_agent(agent)
        await event_bus.publish("agent.heartbeat", {
            "agent_id": agent_id,
            "status": agent.status.value,
        })
        return agent

    async def update_status(self, agent_id: str, status: AgentStatus) -> Agent | None:
        """更新智能体状态"""
        agent = await self.storage.get_agent(agent_id)
        if not agent:
            return None
        agent.status = status
        await self.storage.save_agent(agent)
        await event_bus.publish("agent.status_changed", {
            "agent_id": agent_id,
            "status": status.value,
        })
        return agent

    async def increment_task_count(self, agent_id: str) -> None:
        """增加智能体当前任务数（原子操作，避免并发计数丢失）"""
        await self.storage.adjust_agent_task_count(agent_id, 1)

    async def decrement_task_count(self, agent_id: str) -> None:
        """减少智能体当前任务数（原子操作，下限钳制为 0）"""
        await self.storage.adjust_agent_task_count(agent_id, -1)

    async def find_candidates(
        self,
        required_capabilities: list[str],
        exclude_ids: list[str] | None = None,
    ) -> list[Agent]:
        """
        根据能力需求查找候选智能体，按匹配度+负载排序

        匹配算法：
        - 每个必需能力匹配 +10分
        - 在线状态 +5分
        - 当前任务数越少分数越高 (max 5分)
        """
        exclude_ids = exclude_ids or []
        exclude_set = set(exclude_ids)
        required_lower = [c.lower() for c in required_capabilities]
        agents = await self.storage.list_agents()
        candidates = []

        for agent in agents:
            if agent.agent_id in exclude_set:
                continue
            if agent.status == AgentStatus.OFFLINE or agent.status == AgentStatus.ERROR:
                continue

            # 能力集合预先小写化，避免在内层循环反复构造
            agent_caps_lower = {c.lower() for c in agent.capabilities}

            score = 0
            matched = 0
            for cap in required_lower:
                if cap in agent_caps_lower:
                    score += 10
                    matched += 1

            if required_lower and matched == 0:
                continue  # 不满足任何必需能力则跳过

            if agent.status == AgentStatus.ONLINE:
                score += 5

            # 负载因子：任务数越少分越高 (max 5)
            score += max(0, 5 - agent.current_task_count)

            candidates.append((score, agent))

        # 按分数降序排序；同分时按 agent_id 稳定排序，保证结果可复现
        candidates.sort(key=lambda x: (-x[0], x[1].agent_id))
        return [agent for _, agent in candidates]
