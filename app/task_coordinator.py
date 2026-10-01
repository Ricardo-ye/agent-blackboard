"""任务协调服务 - 任务分配、状态流转与依赖管理"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.models import Task, TaskCreate, TaskUpdate, TaskStatus, Priority
from app.storage import StorageBackend
from app.event_bus import event_bus

if TYPE_CHECKING:
    from app.agent_registry import AgentRegistry


class CircularDependencyError(Exception):
    """循环依赖异常"""


class TaskCoordinator:
    """任务协调器 - 负责任务生命周期管理与智能体分配"""

    def __init__(self, storage: StorageBackend, agent_registry: "AgentRegistry"):
        self.storage = storage
        self.agent_registry = agent_registry

    async def create_task(self, task_create: TaskCreate) -> Task:
        """创建任务"""
        # 验证创建者
        if task_create.creator_id:
            creator = await self.storage.get_agent(task_create.creator_id)
            if not creator:
                raise ValueError(f"Creator agent {task_create.creator_id} not found")

        # 检测循环依赖
        if task_create.dependencies:
            await self._validate_dependencies(task_create.dependencies)

        task = Task(
            task_id=str(uuid.uuid4()),
            title=task_create.title,
            description=task_create.description,
            required_capabilities=task_create.required_capabilities,
            priority=task_create.priority,
            dependencies=task_create.dependencies,
            creator_id=task_create.creator_id,
            deadline=task_create.deadline,
        )
        await self.storage.save_task(task)
        await event_bus.publish("task.created", {
            "task_id": task.task_id,
            "title": task.title,
            "priority": task.priority.value,
            "required_capabilities": task.required_capabilities,
            "creator_id": task.creator_id,
        })
        return task

    async def _validate_dependencies(self, dependency_ids: list[str]) -> None:
        """验证依赖任务存在且无循环"""
        deps = await self.storage.get_tasks(dependency_ids)
        missing = [d for d in dependency_ids if d not in deps]
        if missing:
            raise ValueError(f"Dependency task(s) not found: {missing}")
        # 循环依赖检测通过拓扑排序
        await self._detect_circular_dependencies()

    async def _detect_circular_dependencies(self) -> None:
        """使用DFS检测任务间的循环依赖"""
        tasks = await self.storage.list_tasks()
        graph = {t.task_id: t.dependencies for t in tasks}

        WHITE, GRAY, BLACK = 0, 1, 2
        color = {tid: WHITE for tid in graph}

        async def dfs(node: str) -> None:
            color[node] = GRAY
            for dep in graph.get(node, []):
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    raise CircularDependencyError(
                        f"Circular dependency detected involving {node} and {dep}"
                    )
                if color[dep] == WHITE:
                    await dfs(dep)
            color[node] = BLACK

        for node in graph:
            if color[node] == WHITE:
                await dfs(node)

    async def get_task(self, task_id: str) -> Task | None:
        return await self.storage.get_task(task_id)

    async def list_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        return await self.storage.list_tasks(status)

    async def assign_task(self, task_id: str, assignee_id: str) -> Task:
        """手动分配任务给指定智能体"""
        task = await self.storage.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        agent = await self.storage.get_agent(assignee_id)
        if not agent:
            raise ValueError(f"Agent {assignee_id} not found")

        if not await self._check_dependencies_ready(task):
            raise ValueError("Task dependencies are not yet completed")

        old_assignee = task.assignee_id

        # 幂等保护：重复分配给同一智能体不应重复增加负载计数
        if old_assignee == assignee_id and task.status == TaskStatus.ASSIGNED:
            return task

        task.assignee_id = assignee_id
        task.status = TaskStatus.ASSIGNED
        task.updated_at = datetime.now(timezone.utc)
        await self.storage.save_task(task)

        # 更新智能体负载
        if old_assignee:
            await self.agent_registry.decrement_task_count(old_assignee)
        await self.agent_registry.increment_task_count(assignee_id)

        await event_bus.publish("task.assigned", {
            "task_id": task_id,
            "assignee_id": assignee_id,
            "title": task.title,
        })
        return task

    async def auto_assign(self, task_id: str) -> Task | None:
        """
        自动分配任务 - 基于能力匹配+负载均衡算法

        算法步骤：
        1. 检查依赖是否就绪
        2. 查找满足能力需求的候选智能体（已按匹配度+负载排序）
        3. 分配给最佳候选
        """
        task = await self.storage.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        if task.status != TaskStatus.PENDING:
            return task  # 非待处理任务不自动分配

        if not await self._check_dependencies_ready(task):
            return None  # 依赖未就绪

        candidates = await self.agent_registry.find_candidates(
            task.required_capabilities,
            exclude_ids=[task.assignee_id] if task.assignee_id else None,
        )

        if not candidates:
            return None  # 无可用候选

        # 选择最佳候选（已排序）
        best = candidates[0]
        return await self.assign_task(task_id, best.agent_id)

    async def _check_dependencies_ready(self, task: Task) -> bool:
        """检查任务的所有依赖是否已完成"""
        if not task.dependencies:
            return True
        # 批量取依赖任务，避免逐个查询造成 N+1
        deps = await self.storage.get_tasks(task.dependencies)
        for dep_id in task.dependencies:
            dep = deps.get(dep_id)
            if not dep or dep.status != TaskStatus.DONE:
                return False
        return True

    async def update_task(
        self,
        task_id: str,
        updater_id: str,
        update: TaskUpdate,
    ) -> Task:
        """更新任务状态与字段"""
        task = await self.storage.get_task(task_id)
        if not task:
            raise ValueError(f"Task {task_id} not found")

        old_status = task.status

        # 显式传入的字段集合（区分「传了 None」与「没传」，见 M3 修复）
        provided = update.model_fields_set

        # 更新字段
        if update.title is not None:
            task.title = update.title
        if update.description is not None:
            task.description = update.description
        if update.priority is not None:
            task.priority = update.priority
        # result 允许显式置空
        if "result" in provided:
            task.result = update.result
        if update.deadline is not None:
            task.deadline = update.deadline
        if update.status is not None:
            task.status = update.status

        task.updated_at = datetime.now(timezone.utc)
        await self.storage.save_task(task)

        # 状态变更处理
        if update.status is not None and update.status != old_status:
            await event_bus.publish("task.status_changed", {
                "task_id": task_id,
                "old_status": old_status.value,
                "new_status": update.status.value,
                "assignee_id": task.assignee_id,
                "updater_id": updater_id,
            })

            # 任务完成时释放智能体负载
            if update.status in (TaskStatus.DONE, TaskStatus.FAILED):
                if task.assignee_id:
                    await self.agent_registry.decrement_task_count(task.assignee_id)

                # 检查依赖此任务的下游任务是否可以解锁
                await self._unblock_dependent_tasks(task_id)

        return task

    async def _unblock_dependent_tasks(self, completed_task_id: str) -> None:
        """当任务完成时，检查并解锁依赖此任务的下游任务"""
        # 只扫描 PENDING 状态的任务：已完成/失败/运行中的任务无需解锁，
        # 避免每次任务完成都对全表做无谓遍历。
        pending_tasks = await self.storage.list_tasks(TaskStatus.PENDING)
        for task in pending_tasks:
            if completed_task_id in task.dependencies:
                if await self._check_dependencies_ready(task):
                    # 依赖已全部完成，触发自动分配尝试
                    await event_bus.publish("task.unblocked", {
                        "task_id": task.task_id,
                        "unblocked_by": completed_task_id,
                    })
                    # 尝试自动分配
                    await self.auto_assign(task.task_id)

    async def get_task_assignments(self, agent_id: str) -> list[Task]:
        """获取智能体当前分配的任务"""
        tasks = await self.storage.list_tasks()
        return [t for t in tasks if t.assignee_id == agent_id]
