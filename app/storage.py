"""存储抽象层 - 支持 SQLite / Memory 后端切换"""
from __future__ import annotations

import contextlib
import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from config import DATABASE_PATH
from app.models import (
    Agent, AgentStatus, KnowledgeEntry, Task, TaskStatus,
    Conflict, ConflictStatus, CollaborationRule, Priority,
    ConflictType, RuleTrigger, RuleAction, Stats,
)


class StorageBackend(ABC):
    """存储后端抽象接口"""

    @contextlib.asynccontextmanager
    async def transaction(self):
        """
        批量写入事务（默认空实现）

        后端若支持事务（如 SQLite）可覆写；不支持的后端退化为顺序写入。
        """
        yield self

    # Agent operations
    @abstractmethod
    async def save_agent(self, agent: Agent) -> None: ...
    @abstractmethod
    async def get_agent(self, agent_id: str) -> Agent | None: ...
    @abstractmethod
    async def list_agents(self) -> list[Agent]: ...
    @abstractmethod
    async def delete_agent(self, agent_id: str) -> None: ...
    @abstractmethod
    async def adjust_agent_task_count(self, agent_id: str, delta: int) -> None: ...
    @abstractmethod
    async def mark_agent_offline_if_stale(
        self, agent_id: str, cutoff_iso: str
    ) -> bool: ...
    @abstractmethod
    async def mark_stale_agents_offline(self, cutoff_iso: str) -> list[Agent]: ...

    # Knowledge Entry operations
    @abstractmethod
    async def save_entry(self, entry: KnowledgeEntry) -> None: ...
    @abstractmethod
    async def save_entries(self, entries: list[KnowledgeEntry]) -> None: ...
    @abstractmethod
    async def get_entry(self, entry_id: str) -> KnowledgeEntry | None: ...
    @abstractmethod
    async def list_entries(self, topic: str | None = None) -> list[KnowledgeEntry]: ...
    @abstractmethod
    async def delete_entry(self, entry_id: str) -> None: ...
    @abstractmethod
    async def update_entry_cas(
        self, entry: KnowledgeEntry, expected_version: int
    ) -> bool: ...

    # Task operations
    @abstractmethod
    async def save_task(self, task: Task) -> None: ...
    @abstractmethod
    async def get_task(self, task_id: str) -> Task | None: ...
    @abstractmethod
    async def get_tasks(self, task_ids: list[str]) -> dict[str, Task]: ...
    @abstractmethod
    async def list_tasks(self, status: TaskStatus | None = None) -> list[Task]: ...
    @abstractmethod
    async def delete_task(self, task_id: str) -> None: ...

    # Conflict operations
    @abstractmethod
    async def save_conflict(self, conflict: Conflict) -> None: ...
    @abstractmethod
    async def get_conflict(self, conflict_id: str) -> Conflict | None: ...
    @abstractmethod
    async def list_conflicts(self, status: ConflictStatus | None = None) -> list[Conflict]: ...

    # Rule operations
    @abstractmethod
    async def save_rule(self, rule: CollaborationRule) -> None: ...
    @abstractmethod
    async def get_rule(self, rule_id: str) -> CollaborationRule | None: ...
    @abstractmethod
    async def list_rules(self) -> list[CollaborationRule]: ...
    @abstractmethod
    async def delete_rule(self, rule_id: str) -> None: ...

    # Aggregate operations
    @abstractmethod
    async def get_stats(self) -> Stats: ...


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    # 兼容历史数据（naive）与新数据（aware）：统一补 UTC 时区
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _dumps(obj: Any) -> str:
    """
    JSON 序列化兜底

    content / result / metadata 等字段类型为 Any，可能包含 datetime、
    Decimal、set 等非 JSON 原生类型。直接 json.dumps 会抛 TypeError，
    此处以 default=str 兜底，保证写入不因个别字段类型而整体失败。
    """
    return json.dumps(obj, ensure_ascii=False, default=str)


def _agent_from_row(row: aiosqlite.Row) -> Agent:
    return Agent(
        agent_id=row["agent_id"],
        name=row["name"],
        capabilities=json.loads(row["capabilities"]),
        endpoint=row["endpoint"],
        status=AgentStatus(row["status"]),
        metadata=json.loads(row["metadata"]),
        current_task_count=row["current_task_count"],
        registered_at=_parse_dt(row["registered_at"]),
        last_heartbeat=_parse_dt(row["last_heartbeat"]),
    )


def _entry_from_row(row: aiosqlite.Row) -> KnowledgeEntry:
    return KnowledgeEntry(
        entry_id=row["entry_id"],
        topic=row["topic"],
        content=json.loads(row["content"]),
        author_id=row["author_id"],
        version=row["version"],
        tags=json.loads(row["tags"]),
        priority=Priority(row["priority"]),
        confidence=row["confidence"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _task_from_row(row: aiosqlite.Row) -> Task:
    result_raw = row["result"]
    return Task(
        task_id=row["task_id"],
        title=row["title"],
        description=row["description"],
        required_capabilities=json.loads(row["required_capabilities"]),
        priority=Priority(row["priority"]),
        status=TaskStatus(row["status"]),
        assignee_id=row["assignee_id"],
        dependencies=json.loads(row["dependencies"]),
        result=json.loads(result_raw) if result_raw else None,
        creator_id=row["creator_id"],
        deadline=_parse_dt(row["deadline"]) if row["deadline"] else None,
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _conflict_from_row(row: aiosqlite.Row) -> Conflict:
    return Conflict(
        conflict_id=row["conflict_id"],
        conflict_type=ConflictType(row["conflict_type"]),
        status=ConflictStatus(row["status"]),
        description=row["description"],
        context=json.loads(row["context"]),
        involved_agents=json.loads(row["involved_agents"]),
        resolution=row["resolution"],
        resolved_by=row["resolved_by"],
        created_at=_parse_dt(row["created_at"]),
        resolved_at=_parse_dt(row["resolved_at"]) if row["resolved_at"] else None,
    )


def _rule_from_row(row: aiosqlite.Row) -> CollaborationRule:
    return CollaborationRule(
        rule_id=row["rule_id"],
        name=row["name"],
        trigger=RuleTrigger(row["trigger"]),
        condition=json.loads(row["condition"]),
        action=RuleAction(row["action"]),
        action_params=json.loads(row["action_params"]),
        enabled=bool(row["enabled"]),
        priority=row["priority"],
        created_at=_parse_dt(row["created_at"]),
    )


class SQLiteStorage(StorageBackend):
    """SQLite存储后端实现"""

    def __init__(self, db_path: str = DATABASE_PATH):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None
        self._tx_depth = 0

    async def initialize(self) -> None:
        """初始化数据库连接与表结构"""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        # WAL 模式：读写不互相阻塞，显著改善「多智能体并发写 + 频繁读」场景
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA synchronous=NORMAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._create_tables()

    async def close(self) -> None:
        if self._db:
            await self._db.close()

    @contextlib.asynccontextmanager
    async def transaction(self):
        """
        批量写入事务（支持嵌套）

        进入时暂缓 commit，退出时统一提交；发生异常则回滚。
        在事务块内调用 save_* 方法不会逐条提交，减少 fsync 次数。
        """
        self._tx_depth += 1
        try:
            yield self
        except BaseException:
            self._tx_depth -= 1
            if self._tx_depth == 0:
                await self.db.rollback()
            raise
        else:
            self._tx_depth -= 1
            if self._tx_depth == 0:
                await self._commit()

    async def _commit(self) -> None:
        """在事务块内为空操作，块外立即提交"""
        if self._tx_depth == 0:
            await self.db.commit()

    async def _create_tables(self) -> None:
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS agents (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                capabilities TEXT NOT NULL DEFAULT '[]',
                endpoint TEXT,
                status TEXT NOT NULL DEFAULT 'online',
                metadata TEXT NOT NULL DEFAULT '{}',
                current_task_count INTEGER NOT NULL DEFAULT 0,
                registered_at TEXT NOT NULL,
                last_heartbeat TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS knowledge_entries (
                entry_id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                content TEXT NOT NULL,
                author_id TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                tags TEXT NOT NULL DEFAULT '[]',
                priority TEXT NOT NULL DEFAULT 'normal',
                confidence REAL NOT NULL DEFAULT 1.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_entries_topic ON knowledge_entries(topic);
            CREATE INDEX IF NOT EXISTS idx_entries_topic_updated
                ON knowledge_entries(topic, updated_at DESC);

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                required_capabilities TEXT NOT NULL DEFAULT '[]',
                priority TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'pending',
                assignee_id TEXT,
                dependencies TEXT NOT NULL DEFAULT '[]',
                result TEXT,
                creator_id TEXT,
                deadline TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

            CREATE TABLE IF NOT EXISTS conflicts (
                conflict_id TEXT PRIMARY KEY,
                conflict_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'detected',
                description TEXT NOT NULL,
                context TEXT NOT NULL DEFAULT '{}',
                involved_agents TEXT NOT NULL DEFAULT '[]',
                resolution TEXT,
                resolved_by TEXT,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );

            CREATE TABLE IF NOT EXISTS collaboration_rules (
                rule_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                trigger TEXT NOT NULL,
                condition TEXT NOT NULL DEFAULT '{}',
                action TEXT NOT NULL,
                action_params TEXT NOT NULL DEFAULT '{}',
                enabled INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );

            -- 查询热点索引（IF NOT EXISTS 保证老库可平滑升级）
            CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id);
            CREATE INDEX IF NOT EXISTS idx_agents_status ON agents(status);
            CREATE INDEX IF NOT EXISTS idx_agents_heartbeat ON agents(last_heartbeat);
            CREATE INDEX IF NOT EXISTS idx_agents_status_heartbeat
                ON agents(status, last_heartbeat);
            CREATE INDEX IF NOT EXISTS idx_conflicts_status ON conflicts(status);
            CREATE INDEX IF NOT EXISTS idx_conflicts_type_status
                ON conflicts(conflict_type, status);
            CREATE INDEX IF NOT EXISTS idx_tasks_priority_status ON tasks(priority, status);
            CREATE INDEX IF NOT EXISTS idx_tasks_status_priority ON tasks(status, priority);
        """)
        await self.db.commit()

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage not initialized. Call initialize() first.")
        return self._db

    # ---- Agent operations ----
    async def save_agent(self, agent: Agent) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO agents
               (agent_id, name, capabilities, endpoint, status, metadata,
                current_task_count, registered_at, last_heartbeat)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (agent.agent_id, agent.name, _dumps(agent.capabilities),
             agent.endpoint, agent.status.value, _dumps(agent.metadata),
             agent.current_task_count, agent.registered_at.isoformat(),
             agent.last_heartbeat.isoformat())
        )
        await self._commit()

    async def get_agent(self, agent_id: str) -> Agent | None:
        async with self.db.execute(
            "SELECT * FROM agents WHERE agent_id = ?", (agent_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return _agent_from_row(row) if row else None

    async def list_agents(self) -> list[Agent]:
        async with self.db.execute("SELECT * FROM agents ORDER BY registered_at") as cursor:
            rows = await cursor.fetchall()
            return [_agent_from_row(r) for r in rows]

    async def delete_agent(self, agent_id: str) -> None:
        await self.db.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
        await self._commit()

    async def adjust_agent_task_count(self, agent_id: str, delta: int) -> None:
        """
        原子调整智能体当前任务数

        使用单条 UPDATE 完成 read-modify-write，避免并发下的计数丢失。
        下限钳制在 0，防止计数为负。
        """
        await self.db.execute(
            "UPDATE agents SET current_task_count = MAX(0, current_task_count + ?) "
            "WHERE agent_id = ?",
            (delta, agent_id),
        )
        await self._commit()

    async def mark_agent_offline_if_stale(self, agent_id: str, cutoff_iso: str) -> bool:
        """
        条件更新：仅当智能体仍未离线且心跳早于 cutoff 时才标记离线。

        返回 True 表示本次调用真的完成了状态迁移（调用方据此决定是否发事件），
        从而避免「心跳刚到达却被超时检查覆盖」的竞态。
        """
        cursor = await self.db.execute(
            "UPDATE agents SET status = 'offline' "
            "WHERE agent_id = ? AND status != 'offline' AND last_heartbeat < ?",
            (agent_id, cutoff_iso),
        )
        await self._commit()
        return cursor.rowcount > 0

    async def mark_stale_agents_offline(self, cutoff_iso: str) -> list[Agent]:
        """用单条原子 SQL 批量下线超时智能体。

        RETURNING 返回本次真正发生状态迁移的记录，既避免全表加载和
        逐行 UPDATE，也保留了发布 agent.offline 事件所需的实体信息。
        """
        async with self.db.execute(
            """UPDATE agents
               SET status = 'offline'
               WHERE status != 'offline' AND last_heartbeat < ?
               RETURNING *""",
            (cutoff_iso,),
        ) as cursor:
            rows = await cursor.fetchall()
        await self._commit()
        return [_agent_from_row(row) for row in rows]

    # ---- Knowledge Entry operations ----
    async def save_entry(self, entry: KnowledgeEntry) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO knowledge_entries
               (entry_id, topic, content, author_id, version, tags, priority,
                confidence, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (entry.entry_id, entry.topic, _dumps(entry.content),
             entry.author_id, entry.version, _dumps(entry.tags),
             entry.priority.value, entry.confidence,
             entry.created_at.isoformat(), entry.updated_at.isoformat())
        )
        await self._commit()

    async def save_entries(self, entries: list[KnowledgeEntry]) -> None:
        """使用 executemany 批量写入条目，整批只提交一次。"""
        if not entries:
            return
        await self.db.executemany(
            """INSERT OR REPLACE INTO knowledge_entries
               (entry_id, topic, content, author_id, version, tags, priority,
                confidence, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    entry.entry_id,
                    entry.topic,
                    _dumps(entry.content),
                    entry.author_id,
                    entry.version,
                    _dumps(entry.tags),
                    entry.priority.value,
                    entry.confidence,
                    entry.created_at.isoformat(),
                    entry.updated_at.isoformat(),
                )
                for entry in entries
            ],
        )
        await self._commit()

    async def get_entry(self, entry_id: str) -> KnowledgeEntry | None:
        async with self.db.execute(
            "SELECT * FROM knowledge_entries WHERE entry_id = ?", (entry_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return _entry_from_row(row) if row else None

    async def list_entries(self, topic: str | None = None) -> list[KnowledgeEntry]:
        if topic:
            async with self.db.execute(
                "SELECT * FROM knowledge_entries WHERE topic = ? ORDER BY updated_at DESC",
                (topic,)
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with self.db.execute(
                "SELECT * FROM knowledge_entries ORDER BY updated_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [_entry_from_row(r) for r in rows]

    async def delete_entry(self, entry_id: str) -> None:
        await self.db.execute(
            "DELETE FROM knowledge_entries WHERE entry_id = ?", (entry_id,)
        )
        await self._commit()

    async def update_entry_cas(
        self, entry: KnowledgeEntry, expected_version: int
    ) -> bool:
        """
        乐观锁条件更新（Compare-And-Swap）

        仅当库中 version 仍等于 expected_version 时才写入。
        返回 False 表示版本已被其他写入者抢先推进，本次更新未生效。

        这是消除「读-比较-写」竞态的关键：把版本判定交给单条 SQL，
        由数据库保证判等与写入的原子性。
        """
        cursor = await self.db.execute(
            """UPDATE knowledge_entries
               SET topic = ?, content = ?, tags = ?, priority = ?,
                   confidence = ?, version = ?, updated_at = ?
               WHERE entry_id = ? AND version = ?""",
            (entry.topic, _dumps(entry.content), _dumps(entry.tags),
             entry.priority.value, entry.confidence, entry.version,
             entry.updated_at.isoformat(), entry.entry_id, expected_version)
        )
        await self._commit()
        return cursor.rowcount > 0

    # ---- Task operations ----
    async def save_task(self, task: Task) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO tasks
               (task_id, title, description, required_capabilities, priority,
                status, assignee_id, dependencies, result, creator_id, deadline,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (task.task_id, task.title, task.description,
             _dumps(task.required_capabilities), task.priority.value,
             task.status.value, task.assignee_id,
             _dumps(task.dependencies),
             _dumps(task.result) if task.result is not None else None,
             task.creator_id,
             task.deadline.isoformat() if task.deadline else None,
             task.created_at.isoformat(), task.updated_at.isoformat())
        )
        await self._commit()

    async def get_task(self, task_id: str) -> Task | None:
        async with self.db.execute(
            "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return _task_from_row(row) if row else None

    async def get_tasks(self, task_ids: list[str]) -> dict[str, Task]:
        """批量获取任务，避免依赖检查时的 N+1 查询"""
        if not task_ids:
            return {}
        unique_ids = list(dict.fromkeys(task_ids))
        placeholders = ",".join("?" * len(unique_ids))
        async with self.db.execute(
            f"SELECT * FROM tasks WHERE task_id IN ({placeholders})",
            tuple(unique_ids),
        ) as cursor:
            rows = await cursor.fetchall()
        return {r["task_id"]: _task_from_row(r) for r in rows}

    async def list_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        if status:
            async with self.db.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY created_at",
                (status.value,)
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with self.db.execute(
                "SELECT * FROM tasks ORDER BY created_at"
            ) as cursor:
                rows = await cursor.fetchall()
        return [_task_from_row(r) for r in rows]

    async def delete_task(self, task_id: str) -> None:
        await self.db.execute("DELETE FROM tasks WHERE task_id = ?", (task_id,))
        await self._commit()

    # ---- Conflict operations ----
    async def save_conflict(self, conflict: Conflict) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO conflicts
               (conflict_id, conflict_type, status, description, context,
                involved_agents, resolution, resolved_by, created_at, resolved_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (conflict.conflict_id, conflict.conflict_type.value,
             conflict.status.value, conflict.description,
             _dumps(conflict.context),
             _dumps(conflict.involved_agents),
             conflict.resolution, conflict.resolved_by,
             conflict.created_at.isoformat(),
             conflict.resolved_at.isoformat() if conflict.resolved_at else None)
        )
        await self._commit()

    async def get_conflict(self, conflict_id: str) -> Conflict | None:
        async with self.db.execute(
            "SELECT * FROM conflicts WHERE conflict_id = ?", (conflict_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return _conflict_from_row(row) if row else None

    async def list_conflicts(self, status: ConflictStatus | None = None) -> list[Conflict]:
        if status:
            async with self.db.execute(
                "SELECT * FROM conflicts WHERE status = ? ORDER BY created_at DESC",
                (status.value,)
            ) as cursor:
                rows = await cursor.fetchall()
        else:
            async with self.db.execute(
                "SELECT * FROM conflicts ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
        return [_conflict_from_row(r) for r in rows]

    # ---- Rule operations ----
    async def save_rule(self, rule: CollaborationRule) -> None:
        await self.db.execute(
            """INSERT OR REPLACE INTO collaboration_rules
               (rule_id, name, trigger, condition, action, action_params,
                enabled, priority, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rule.rule_id, rule.name, rule.trigger.value,
             _dumps(rule.condition), rule.action.value,
             _dumps(rule.action_params), int(rule.enabled),
             rule.priority, rule.created_at.isoformat())
        )
        await self._commit()

    async def get_rule(self, rule_id: str) -> CollaborationRule | None:
        async with self.db.execute(
            "SELECT * FROM collaboration_rules WHERE rule_id = ?", (rule_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return _rule_from_row(row) if row else None

    async def list_rules(self) -> list[CollaborationRule]:
        async with self.db.execute(
            "SELECT * FROM collaboration_rules ORDER BY priority DESC, created_at"
        ) as cursor:
            rows = await cursor.fetchall()
            return [_rule_from_row(r) for r in rows]

    async def delete_rule(self, rule_id: str) -> None:
        await self.db.execute(
            "DELETE FROM collaboration_rules WHERE rule_id = ?", (rule_id,)
        )
        await self._commit()

    async def get_stats(self) -> Stats:
        """
        在 SQLite 中直接聚合统计，避免为一个计数请求构造所有领域对象。
        """
        async with self.db.execute(
            """SELECT
                (SELECT COUNT(*) FROM agents) AS total_agents,
                (SELECT COUNT(*) FROM agents WHERE status = 'online') AS online_agents,
                (SELECT COUNT(*) FROM knowledge_entries) AS total_entries,
                (SELECT COUNT(*) FROM tasks) AS total_tasks,
                (SELECT COUNT(*) FROM tasks WHERE status = 'pending') AS pending_tasks,
                (SELECT COUNT(*) FROM tasks WHERE status = 'done') AS completed_tasks,
                (SELECT COUNT(*) FROM conflicts) AS total_conflicts,
                (SELECT COUNT(*) FROM conflicts WHERE status = 'resolved') AS resolved_conflicts,
                (SELECT COUNT(*) FROM collaboration_rules) AS total_rules"""
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("Failed to aggregate database statistics")
        return Stats(
            total_agents=row["total_agents"],
            online_agents=row["online_agents"],
            total_entries=row["total_entries"],
            total_tasks=row["total_tasks"],
            pending_tasks=row["pending_tasks"],
            completed_tasks=row["completed_tasks"],
            total_conflicts=row["total_conflicts"],
            resolved_conflicts=row["resolved_conflicts"],
            total_rules=row["total_rules"],
        )
