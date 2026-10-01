# 智能体动态协作黑板系统 - 架构设计文档

## 1. 系统概述

本系统基于经典**黑板模式（Blackboard Pattern）**设计，旨在为多个异构智能体提供一个共享的协作空间。系统支持智能体动态注册与注销、实时信息共享、任务自动分配、冲突检测与解决、以及协作规则的动态配置。

### 1.1 核心设计思想

黑板模式由三个经典组件构成：

| 组件 | 本系统对应模块 | 职责 |
|------|---------------|------|
| **黑板（Blackboard）** | 黑板核心服务 | 共享数据结构，存储智能体发布的知识、任务、状态 |
| **知识源（Knowledge Sources）** | 智能体管理服务 | 各个注册的智能体，通过读写黑板进行协作 |
| **控制壳（Control Shell）** | 协调调度服务 | 监控黑板变化，触发任务分配，检测并解决冲突 |

## 2. 技术选型

| 层次 | 技术选型 | 选型理由 |
|------|---------|---------|
| 编程语言 | Python 3.11+ | AI/智能体生态最丰富，异步支持完善，开发效率高 |
| Web框架 | FastAPI | 原生异步支持、WebSocket、自动生成OpenAPI文档、性能优异 |
| 数据建模 | Pydantic v2 | 类型安全、数据验证、序列化/反序列化 |
| 持久化 | SQLite (aiosqlite) | 零配置、单文件部署、ACID事务；生产可平滑切换PostgreSQL |
| 实时通信 | WebSocket + 内存Pub/Sub | 双向实时推送；抽象层支持切换Redis Pub/Sub |
| 存储抽象 | 自定义Storage接口 | 支持Memory/SQLite/Redis多后端切换 |
| 测试 | pytest + httpx | 异步测试支持完善 |

### 2.1 架构分层

```
┌─────────────────────────────────────────────────────────────┐
│                      表现层 (Presentation)                   │
│         REST API (FastAPI)  │  WebSocket 实时通道            │
├─────────────────────────────────────────────────────────────┤
│                      服务层 (Services)                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ 智能体管理 │ │ 黑板核心  │ │ 任务协调  │ │  冲突解决器   │  │
│  │ Service  │ │ Service  │ │ Service  │ │  Resolver    │  │
│  └──────────┘ └──────────┘ └──────────┘ └──────────────┘  │
│  ┌──────────────────┐ ┌──────────────────────────────────┐ │
│  │ 协作规则引擎      │ │      事件总线 (Event Bus)        │ │
│  │ Rule Engine      │ │  异步Pub/Sub + WebSocket推送      │ │
│  └──────────────────┘ └──────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────┤
│                      数据层 (Data)                            │
│     Storage抽象接口  →  SQLite / Memory / Redis (可选)       │
└─────────────────────────────────────────────────────────────┘
```

## 3. 核心模块设计

### 3.1 智能体管理服务 (AgentRegistry)

**职责**：管理智能体的注册、注销、心跳、能力描述与状态追踪。

**核心数据结构**：
```python
class Agent(BaseModel):
    agent_id: str              # 唯一标识
    name: str                  # 显示名称
    capabilities: list[str]    # 能力标签，如 ["nlp", "code_gen", "search"]
    endpoint: str | None       # Webhook回调地址
    status: AgentStatus        # ONLINE / OFFLINE / BUSY / ERROR
    metadata: dict             # 自定义元数据
    registered_at: datetime
    last_heartbeat: datetime
```

**关键算法**：
- **心跳超时检测**：后台定时任务扫描，超过 `heartbeat_timeout` 阈值标记为 OFFLINE
- **能力匹配**：基于标签的加权匹配算法，为任务分配筛选候选智能体

### 3.2 黑板核心服务 (BlackboardCore)

**职责**：维护共享数据空间，支持知识条目（Knowledge Entry）的CRUD与版本管理。

**核心数据结构**：
```python
class KnowledgeEntry(BaseModel):
    entry_id: str
    topic: str                 # 主题分类，如 "design", "code", "test"
    content: Any               # 内容（支持任意JSON）
    author_id: str             # 发布者agent_id
    version: int               # 版本号，乐观锁
    tags: list[str]
    priority: Priority         # LOW / NORMAL / HIGH / CRITICAL
    created_at: datetime
    updated_at: datetime
```

**关键算法**：
- **乐观锁版本控制**：更新时校验版本号，冲突时拒绝并返回最新版本
- **主题订阅过滤**：智能体可订阅特定主题，变更时精准推送
- **增量同步**：基于 `updated_at` 的时间戳游标支持增量拉取

### 3.3 任务协调服务 (TaskCoordinator)

**职责**：任务创建、分解、分配、状态流转与依赖管理。

**核心数据结构**：
```python
class Task(BaseModel):
    task_id: str
    title: str
    description: str
    required_capabilities: list[str]  # 所需能力
    priority: Priority
    status: TaskStatus                # PENDING/ASSIGNED/IN_PROGRESS/REVIEW/DONE/BLOCKED
    assignee_id: str | None
    dependencies: list[str]           # 依赖的task_id列表
    result: Any | None
    created_at: datetime
    deadline: datetime | None
```

**关键算法**：
- **基于能力的任务分配**：匹配 `required_capabilities` 与智能体 `capabilities`，按匹配度+负载排序
- **依赖拓扑排序**：检测循环依赖，按拓扑顺序解锁可执行任务
- **负载均衡**：优先分配给当前任务数最少的可用智能体

### 3.4 冲突解决器 (ConflictResolver)

**职责**：检测并解决多智能体协作中的冲突。

**冲突类型**：
| 类型 | 检测方式 | 解决策略 |
|------|---------|---------|
| 写冲突 | 同一entry并发更新 | 乐观锁+版本号，后写者重试或合并 |
| 任务分配冲突 | 多任务争抢同一智能体 | 优先级抢占 + 排队等待 |
| 资源冲突 | 多智能体请求互斥资源 | 基于规则的仲裁（先到先得/优先级） |
| 意见冲突 | 同一主题矛盾知识 | 置信度加权投票 + 人工介入标记 |

**核心算法**：
- **冲突检测**：基于事件流的规则匹配，当检测到冲突模式时触发
- **解决策略链**：按优先级尝试策略（版本合并→优先级仲裁→投票→升级人工）
- **冲突记录**：完整记录冲突上下文与解决过程，支持事后分析

### 3.5 协作规则引擎 (RuleEngine)

**职责**：支持动态配置协作规则，无需重启即可生效。

**规则模型**：
```python
class CollaborationRule(BaseModel):
    rule_id: str
    name: str
    trigger: RuleTrigger       # 事件类型：on_task_created, on_conflict, ...
    condition: dict            # 条件表达式，如 {"priority": "HIGH"}
    action: RuleAction         # 动作：auto_assign, escalate, notify, ...
    action_params: dict        # 动作参数
    enabled: bool
    priority: int              # 规则执行优先级
```

**关键算法**：
- **规则匹配**：事件触发时，按 `priority` 排序遍历匹配条件，执行第一个匹配的动作链
- **热加载**：规则变更后即时生效，通过事件总线通知所有节点
- **规则链**：支持多规则链式执行，前一规则的输出可作为后一规则的输入

## 4. 模块间交互关系

```
                    ┌──────────────┐
                    │   WebSocket  │
                    │   Clients    │
                    └──────┬───────┘
                           │ 实时推送
                    ┌──────▼───────┐
                    │  Event Bus   │◄──────────┐
                    └──────┬───────┘           │
                           │ 事件发布          │
           ┌───────────────┼───────────────┐   │
           │               │               │   │
    ┌──────▼──────┐ ┌──────▼──────┐ ┌─────▼───┴─┐
    │ Blackboard  │ │   Task      │ │  Conflict  │
    │   Core      │ │ Coordinator │ │  Resolver  │
    └──────┬──────┘ └──────┬──────┘ └─────┬─────┘
           │               │               │
           │    ┌──────────┘               │
           │    │                          │
    ┌──────▼────▼──────┐          ┌───────▼───────┐
    │  Agent Registry  │          │  Rule Engine  │
    └──────────────────┘          └───────────────┘
           │                              │
           └──────────┬───────────────────┘
                      │
               ┌──────▼──────┐
               │  Storage    │
               │  (SQLite)   │
               └─────────────┘
```

**典型协作流程**：
1. 智能体通过 REST API 注册，声明能力
2. 某智能体创建任务并发布到黑板
3. TaskCoordinator 检测新任务，RuleEngine 评估分配规则
4. 匹配最佳智能体并分配，通过 Event Bus 实时通知
5. 智能体执行任务，将结果写入黑板（KnowledgeEntry）
6. 若发生冲突，ConflictResolver 介入解决
7. 任务完成后触发后续依赖任务

## 5. 数据持久化设计

### 5.1 存储抽象接口

```python
class StorageBackend(ABC):
    async def save_agent(self, agent: Agent) -> None: ...
    async def get_agent(self, agent_id: str) -> Agent | None: ...
    async def list_agents(self) -> list[Agent]: ...
    async def delete_agent(self, agent_id: str) -> None: ...
    # ... entries, tasks, rules, conflicts
```

### 5.2 数据库表结构（SQLite）

- `agents` - 智能体注册表
- `knowledge_entries` - 黑板知识条目表
- `tasks` - 任务表
- `collaboration_rules` - 协作规则表
- `conflicts` - 冲突记录表
- `events` - 事件日志表（用于审计与回放）

## 6. 实时通信机制

### 6.1 WebSocket事件协议

```json
// 服务端 → 客户端
{
  "event": "task.assigned",
  "data": { "task_id": "...", "assignee_id": "..." },
  "timestamp": "2026-09-06T10:00:00Z"
}

// 客户端 → 服务端
{
  "action": "subscribe",
  "channels": ["tasks", "entries:design"]
}
```

### 6.2 订阅频道模型

- `agent:{agent_id}` - 智能体专属事件
- `tasks` - 所有任务变更
- `entries:{topic}` - 特定主题知识变更
- `conflicts` - 冲突事件
- `rules` - 规则变更

## 7. 非功能性需求

| 指标 | 目标 |
|------|------|
| API响应时间 | P95 < 100ms (本地) |
| WebSocket推送延迟 | < 50ms |
| 并发智能体数 | 100+ |
| 系统可用性 | 99% (MVP阶段) |
| 数据持久化 | 所有状态变更持久化，支持崩溃恢复 |

## 8. 项目结构

```
agent-tree/
├── ARCHITECTURE.md          # 本文档
├── README.md
├── requirements.txt
├── main.py                  # FastAPI入口
├── config.py                # 配置
├── app/
│   ├── __init__.py
│   ├── models.py            # Pydantic数据模型
│   ├── storage.py           # 存储抽象 + SQLite实现
│   ├── event_bus.py         # 事件总线
│   ├── agent_registry.py    # 智能体管理
│   ├── blackboard.py        # 黑板核心
│   ├── task_coordinator.py  # 任务协调
│   ├── conflict_resolver.py # 冲突解决
│   ├── rule_engine.py       # 规则引擎
│   ├── api/
│   │   ├── __init__.py
│   │   ├── agents.py        # 智能体API
│   │   ├── entries.py       # 黑板条目API
│   │   ├── tasks.py         # 任务API
│   │   ├── rules.py         # 规则API
│   │   ├── conflicts.py     # 冲突API
│   │   └── websocket.py     # WebSocket接口
│   └── services.py          # 服务聚合层
├── tests/
│   ├── conftest.py
│   ├── test_agents.py
│   ├── test_blackboard.py
│   ├── test_tasks.py
│   ├── test_conflicts.py
│   ├── test_rules.py
│   └── test_integration.py
└── reports/
    ├── reflective_analysis.md
    ├── performance_test_report.md
    └── optimal_implementation_plan.md
```
