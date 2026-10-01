"""黑板核心服务 - 共享数据空间管理"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models import (
    KnowledgeEntry, EntryCreate, EntryUpdate, Priority, priority_rank,
)
from app.storage import StorageBackend
from app.event_bus import event_bus


class VersionConflictError(Exception):
    """版本冲突异常 - 乐观锁失败"""
    def __init__(self, current_version: int, message: str = "Version conflict"):
        super().__init__(message)
        self.current_version = current_version


class BlackboardCore:
    """黑板核心 - 维护共享知识数据空间"""

    def __init__(self, storage: StorageBackend):
        self.storage = storage

    async def create_entry(self, author_id: str, entry_create: EntryCreate) -> KnowledgeEntry:
        """创建知识条目"""
        # 验证作者存在
        author = await self.storage.get_agent(author_id)
        if not author:
            raise ValueError(f"Agent {author_id} not found")

        entry = KnowledgeEntry(
            entry_id=str(uuid.uuid4()),
            topic=entry_create.topic,
            content=entry_create.content,
            author_id=author_id,
            tags=entry_create.tags,
            priority=entry_create.priority,
            confidence=entry_create.confidence,
        )
        await self.storage.save_entry(entry)
        await event_bus.publish("entry.created", {
            "entry_id": entry.entry_id,
            "topic": entry.topic,
            "author_id": author_id,
            "tags": entry.tags,
            "priority": entry.priority.value,
        })
        return entry

    async def create_entries_batch(
        self,
        author_id: str,
        entry_creates: list[EntryCreate],
    ) -> list[KnowledgeEntry]:
        """批量创建知识条目。

        作者只校验一次，并由存储层以单次事务提交整批数据。事件在
        数据持久化成功后才发布，避免回滚的条目被订阅者看见。
        """
        if not entry_creates:
            return []

        author = await self.storage.get_agent(author_id)
        if not author:
            raise ValueError(f"Agent {author_id} not found")

        entries = [
            KnowledgeEntry(
                entry_id=str(uuid.uuid4()),
                topic=item.topic,
                content=item.content,
                author_id=author_id,
                tags=item.tags,
                priority=item.priority,
                confidence=item.confidence,
            )
            for item in entry_creates
        ]
        await self.storage.save_entries(entries)

        # 同一 topic 的整批数据已经同时可见，只在该 topic 的最后一条
        # 事件上触发冲突扫描，避免批量导入退化成 O(n²) 重复查询。
        last_index_by_topic = {
            entry.topic: index for index, entry in enumerate(entries)
        }
        for index, entry in enumerate(entries):
            await event_bus.publish("entry.created", {
                "entry_id": entry.entry_id,
                "topic": entry.topic,
                "author_id": author_id,
                "tags": entry.tags,
                "priority": entry.priority.value,
                "check_conflicts": last_index_by_topic[entry.topic] == index,
            })
        return entries

    async def update_entry(
        self,
        entry_id: str,
        author_id: str,
        update: EntryUpdate,
    ) -> KnowledgeEntry:
        """
        更新知识条目 - 使用乐观锁版本控制

        如果版本不匹配，抛出 VersionConflictError

        并发安全：版本判定通过 storage.update_entry_cas 下沉到单条条件 UPDATE，
        由数据库保证「判等 + 写入」的原子性。原先的「读版本 → 比较 → 写回」
        在并发下存在 TOCTOU 窗口，多个请求可同时通过校验并互相覆盖。
        """
        entry = await self.storage.get_entry(entry_id)
        if not entry:
            raise ValueError(f"Entry {entry_id} not found")

        expected_version = entry.version

        # 先做一次前置校验，快速失败（避免无谓的写尝试），但不作为唯一防线
        if update.version != expected_version:
            raise VersionConflictError(
                current_version=expected_version,
                message=(
                    f"Version conflict: expected {update.version}, "
                    f"current is {expected_version}"
                ),
            )

        # 更新字段
        entry.content = update.content
        entry.version = expected_version + 1
        entry.updated_at = datetime.now(timezone.utc)
        if update.tags is not None:
            entry.tags = update.tags
        if update.priority is not None:
            entry.priority = update.priority
        if update.confidence is not None:
            entry.confidence = update.confidence

        # 原子条件写入：期间若被其他写入者抢先，则本次失败
        applied = await self.storage.update_entry_cas(entry, expected_version)
        if not applied:
            latest = await self.storage.get_entry(entry_id)
            current = latest.version if latest else expected_version
            raise VersionConflictError(
                current_version=current,
                message=(
                    f"Version conflict: expected {update.version}, "
                    f"current is {current}"
                ),
            )

        await event_bus.publish("entry.updated", {
            "entry_id": entry.entry_id,
            "topic": entry.topic,
            "author_id": entry.author_id,
            "version": entry.version,
            "updated_by": author_id,
        })
        return entry

    async def get_entry(self, entry_id: str) -> KnowledgeEntry | None:
        return await self.storage.get_entry(entry_id)

    async def list_entries(self, topic: str | None = None) -> list[KnowledgeEntry]:
        return await self.storage.list_entries(topic)

    async def delete_entry(self, entry_id: str, author_id: str) -> bool:
        """删除知识条目 - 仅作者可删除"""
        entry = await self.storage.get_entry(entry_id)
        if not entry:
            return False
        if entry.author_id != author_id:
            raise PermissionError("Only the author can delete this entry")
        await self.storage.delete_entry(entry_id)
        await event_bus.publish("entry.deleted", {
            "entry_id": entry_id,
            "topic": entry.topic,
            "author_id": author_id,
        })
        return True

    async def merge_entries(
        self,
        source_ids: list[str],
        target_topic: str,
        author_id: str,
    ) -> KnowledgeEntry:
        """
        合并多个条目为一个新条目 - 用于解决意见冲突

        合并策略：
        - 收集所有条目的内容
        - 计算加权平均置信度
        - 保留所有标签

        幂等保证：若同一组 source_ids 已合并过，直接返回既有合并条目，
        避免重复合并造成 merged 条目不断累积（原实现的缺陷）。
        """
        entries = []
        for eid in source_ids:
            entry = await self.storage.get_entry(eid)
            if entry:
                entries.append(entry)

        if not entries:
            raise ValueError("No valid entries to merge")

        # 幂等检查：复用同一来源集合此前产出的合并条目
        canonical_sources = sorted(source_ids)
        existing = await self._find_merged_entry(canonical_sources, target_topic)
        if existing is not None:
            return existing

        # 合并内容
        merged_content = {
            "merged_from": source_ids,
            "contents": [e.content for e in entries],
        }

        # 加权平均置信度
        total_confidence = sum(e.confidence for e in entries)
        avg_confidence = total_confidence / len(entries)

        # 合并标签（去重后排序，保证结果稳定）
        merged_tags = sorted(set(tag for e in entries for tag in e.tags))

        merged_entry = KnowledgeEntry(
            entry_id=str(uuid.uuid4()),
            topic=target_topic,
            content=merged_content,
            author_id=author_id,
            confidence=avg_confidence,
            tags=merged_tags,
            # 取语义上最高优先级（不能直接比 str 枚举，会退化为字母序）
            priority=max(entries, key=lambda e: priority_rank(e.priority)).priority,
        )
        await self.storage.save_entry(merged_entry)
        await event_bus.publish("entry.merged", {
            "entry_id": merged_entry.entry_id,
            "source_ids": source_ids,
            "topic": target_topic,
        })
        return merged_entry

    async def _find_merged_entry(
        self,
        canonical_sources: list[str],
        target_topic: str,
    ) -> KnowledgeEntry | None:
        """查找由同一组来源条目合并而来的既有条目（用于幂等复用）"""
        for candidate in await self.storage.list_entries(target_topic):
            content = candidate.content
            if not isinstance(content, dict):
                continue
            if "merged_from" not in content:
                continue
            if sorted(content.get("merged_from") or []) == canonical_sources:
                return candidate
        return None
