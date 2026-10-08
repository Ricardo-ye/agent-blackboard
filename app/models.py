"""核心数据模型定义"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any
from pydantic import BaseModel, Field, StringConstraints


# API 输入字符串的公共边界。仅约束外部创建/更新模型，避免历史持久化数据
# 因新增校验而无法读取。
NameText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
TopicText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)
]
ShortText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
DescriptionText = Annotated[str, StringConstraints(max_length=8192)]


class AgentStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    BUSY = "busy"
    ERROR = "error"


class Priority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


# 优先级的语义序（数值越大越紧急）
# 注意：Priority 继承 str，直接比较或用 max() 会退化为字母序
# （'critical' < 'high' < 'low' < 'normal'），与业务语义相反。
# 需要比较优先级时一律通过此映射，不要直接比较枚举值。
PRIORITY_ORDER: dict[str, int] = {
    Priority.LOW.value: 0,
    Priority.NORMAL.value: 1,
    Priority.HIGH.value: 2,
    Priority.CRITICAL.value: 3,
}


def priority_rank(priority: "Priority") -> int:
    """返回优先级的语义序号，用于排序与比较。"""
    return PRIORITY_ORDER.get(priority.value, 0)


class TaskStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


class ConflictType(str, Enum):
    WRITE_CONFLICT = "write_conflict"
    TASK_ASSIGNMENT = "task_assignment"
    RESOURCE_CONFLICT = "resource_conflict"
    OPINION_CONFLICT = "opinion_conflict"


class ConflictStatus(str, Enum):
    DETECTED = "detected"
    RESOLVING = "resolving"
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    DISMISSED = "dismissed"


# 冲突终态：进入后不可再被解决。
CONFLICT_TERMINAL_STATUSES: frozenset["ConflictStatus"] = frozenset({
    ConflictStatus.RESOLVED,
    ConflictStatus.DISMISSED,
})

# 冲突活跃态：尚未终结、仍需跟踪。用于冲突检测的幂等判定，
# 必须包含 ESCALATED —— 它表示「已升级待人工处理」，问题依然存在。
CONFLICT_ACTIVE_STATUSES: frozenset["ConflictStatus"] = frozenset({
    ConflictStatus.DETECTED,
    ConflictStatus.RESOLVING,
    ConflictStatus.ESCALATED,
})


class RuleTrigger(str, Enum):
    ON_TASK_CREATED = "on_task_created"
    ON_TASK_STATUS_CHANGED = "on_task_status_changed"
    ON_CONFLICT_DETECTED = "on_conflict_detected"
    ON_AGENT_REGISTERED = "on_agent_registered"
    ON_ENTRY_CREATED = "on_entry_created"
    ON_ENTRY_UPDATED = "on_entry_updated"


class RuleAction(str, Enum):
    AUTO_ASSIGN = "auto_assign"
    ESCALATE = "escalate"
    NOTIFY = "notify"
    SET_PRIORITY = "set_priority"
    BLOCK_TASK = "block_task"
    MERGE_ENTRIES = "merge_entries"
    VOTE_RESOLUTION = "vote_resolution"


class Agent(BaseModel):
    """智能体模型"""
    agent_id: str
    name: str
    capabilities: list[str] = Field(default_factory=list)
    endpoint: str | None = None
    status: AgentStatus = AgentStatus.ONLINE
    metadata: dict[str, Any] = Field(default_factory=dict)
    current_task_count: int = 0
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_heartbeat: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AgentCreate(BaseModel):
    name: NameText
    capabilities: list[ShortText] = Field(default_factory=list, max_length=64)
    endpoint: str | None = Field(default=None, max_length=2048)
    metadata: dict[str, Any] = Field(default_factory=dict)


class KnowledgeEntry(BaseModel):
    """黑板知识条目模型"""
    entry_id: str
    topic: str
    content: Any
    author_id: str
    version: int = 1
    tags: list[str] = Field(default_factory=list)
    priority: Priority = Priority.NORMAL
    confidence: float = 1.0  # 置信度，用于意见冲突投票
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class EntryCreate(BaseModel):
    topic: TopicText
    content: Any
    tags: list[ShortText] = Field(default_factory=list, max_length=128)
    priority: Priority = Priority.NORMAL
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class EntryUpdate(BaseModel):
    content: Any
    version: int = Field(ge=1)  # 乐观锁
    tags: list[ShortText] | None = Field(default=None, max_length=128)
    priority: Priority | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class Task(BaseModel):
    """任务模型"""
    task_id: str
    title: str
    description: str = ""
    required_capabilities: list[str] = Field(default_factory=list)
    priority: Priority = Priority.NORMAL
    status: TaskStatus = TaskStatus.PENDING
    assignee_id: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    result: Any | None = None
    creator_id: str | None = None
    deadline: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class TaskCreate(BaseModel):
    title: NameText
    description: DescriptionText = ""
    required_capabilities: list[ShortText] = Field(default_factory=list, max_length=64)
    priority: Priority = Priority.NORMAL
    dependencies: list[ShortText] = Field(default_factory=list, max_length=256)
    creator_id: ShortText | None = None
    deadline: datetime | None = None


class TaskUpdate(BaseModel):
    title: NameText | None = None
    description: DescriptionText | None = None
    priority: Priority | None = None
    status: TaskStatus | None = None
    result: Any | None = None
    deadline: datetime | None = None


class Conflict(BaseModel):
    """冲突记录模型"""
    conflict_id: str
    conflict_type: ConflictType
    status: ConflictStatus = ConflictStatus.DETECTED
    description: str
    context: dict[str, Any] = Field(default_factory=dict)
    involved_agents: list[str] = Field(default_factory=list)
    resolution: str | None = None
    resolved_by: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None


class CollaborationRule(BaseModel):
    """协作规则模型"""
    rule_id: str
    name: str
    trigger: RuleTrigger
    condition: dict[str, Any] = Field(default_factory=dict)
    action: RuleAction
    action_params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    priority: int = 0  # 规则执行优先级，数字越大越先执行
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RuleCreate(BaseModel):
    name: NameText
    trigger: RuleTrigger
    condition: dict[str, Any] = Field(default_factory=dict)
    action: RuleAction
    action_params: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    priority: int = Field(default=0, ge=-1_000_000, le=1_000_000)


class Event(BaseModel):
    """事件模型 - 用于事件总线"""
    event_id: str
    event_type: str
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str | None = None


class Stats(BaseModel):
    """系统统计"""
    total_agents: int = 0
    online_agents: int = 0
    total_entries: int = 0
    total_tasks: int = 0
    pending_tasks: int = 0
    completed_tasks: int = 0
    total_conflicts: int = 0
    resolved_conflicts: int = 0
    total_rules: int = 0
