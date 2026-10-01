# 黑板系统最优制作方案

> 基于系统设计、开发实现、测试验证与反思分析的完整方案文档

---

## 一、系统整体技术架构设计

### 1.1 架构总览

本系统采用经典**黑板模式（Blackboard Pattern）**，结合现代异步Web技术栈，构建支持异构智能体动态协作的实时系统。

```
┌─────────────────────────────────────────────────────────────┐
│                      表现层 (Presentation)                   │
│    REST API (FastAPI)     │     WebSocket 实时通道           │
│    /api/agents            │     /ws                         │
│    /api/entries           │     事件推送 < 25ms             │
│    /api/tasks             │                                  │
│    /api/rules             │                                  │
│    /api/conflicts         │                                  │
├─────────────────────────────────────────────────────────────┤
│                      服务层 (Services)                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────────┐  │
│  │ Agent    │ │ Blackboard│ │ Task     │ │ Conflict     │  │
│  │ Registry │ │ Core      │ │ Coord.   │ │ Resolver     │  │
│  │          │ │           │ │          │ │              │  │
│  │ 注册/心跳 │ │ 知识条目   │ │ 分配/依赖 │ │ 检测/解决     │  │
│  │ 能力匹配 │ │ 乐观锁     │ │ 状态流转 │ │ 策略链       │  │
│  └────┬─────┘ └─────┬─────┘ └────┬─────┘ └──────┬───────┘  │
│       │             │             │              │          │
│  ┌────┴─────────────┴─────────────┴──────────────┴───────┐  │
│  │              Rule Engine (协作规则引擎)                │  │
│  │   条件匹配 | 动作执行 | 热加载 | 优先级链              │  │
│  └──────────────────────┬────────────────────────────────┘  │
│                         │                                     │
│  ┌──────────────────────┴────────────────────────────────┐  │
│  │              Event Bus (事件总线)                      │  │
│  │   异步Pub/Sub | 频道订阅 | WebSocket推送              │  │
│  └──────────────────────┬────────────────────────────────┘  │
├─────────────────────────┼───────────────────────────────────┤
│                      数据层 (Data)                            │
│     Storage抽象接口 ──┬── SQLite (当前)                      │
│                        ├── PostgreSQL (生产可选)            │
│                        └── Redis (缓存/Pub/Sub可选)          │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 模块间交互关系

**核心交互流程**：

```
智能体A注册 → AgentRegistry → event_bus.publish("agent.registered")
                                    ↓
                              RuleEngine评估规则
                                    ↓
智能体A创建任务 → TaskCoordinator → event_bus.publish("task.created")
                                    ↓
                              RuleEngine触发auto_assign
                                    ↓
                    AgentRegistry.find_candidates() 能力匹配
                                    ↓
                              分配给智能体B
                                    ↓
                              event_bus推送WebSocket
                                    ↓
智能体B执行任务 → BlackboardCore.create_entry() 发布结果
                                    ↓
                              ConflictResolver检测冲突
                                    ↓
                              RuleEngine评估冲突规则
```

**事件总线频道模型**：

| 频道 | 订阅者 | 推送事件 |
|------|--------|---------|
| `agent:{id}` | 特定智能体 | 分配任务、状态变更 |
| `tasks` | 所有观察者 | 任务创建/状态变更 |
| `entries:{topic}` | 主题订阅者 | 知识条目变更 |
| `conflicts` | 监控者 | 冲突检测/解决 |
| `rules` | 管理员 | 规则变更 |

### 1.3 部署架构

```
┌─────────────┐     ┌──────────────────┐     ┌─────────────┐
│   Nginx     │────▶│  FastAPI + Uvicorn│────▶│   SQLite    │
│  (反向代理)  │     │  (ASGI多worker)   │     │  (数据文件)  │
└─────────────┘     └────────┬─────────┘     └─────────────┘
                             │
                     ┌───────┴───────┐
                     │  WebSocket     │
                     │  客户端集群     │
                     └───────────────┘
```

---

## 二、开发工具与环境配置

### 2.1 技术栈版本

| 组件 | 版本 | 用途 |
|------|------|------|
| Python | 3.11.9 | 运行时 |
| FastAPI | 0.115.6 | Web框架 |
| Uvicorn | 0.34.0 | ASGI服务器 |
| Pydantic | 2.10.4 | 数据建模与验证 |
| aiosqlite | 0.20.0 | 异步SQLite驱动 |
| httpx | 0.28.1 | 异步HTTP客户端（测试） |
| pytest | 8.3.4 | 测试框架 |
| pytest-asyncio | 0.25.2 | 异步测试支持 |
| websockets | 14.1 | WebSocket客户端/服务端 |

### 2.2 环境配置步骤

```bash
# 1. 克隆项目
git clone <repository-url>
cd agent-tree

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate     # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 启动服务
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 5. 运行测试
pytest tests/ -v

# 6. 性能测试
python tests/performance_test.py
```

### 2.3 环境变量配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BLACKBOARD_DB_PATH` | `data/blackboard.db` | 数据库文件路径 |
| `HEARTBEAT_TIMEOUT` | `60` | 心跳超时秒数 |
| `HEARTBEAT_CHECK_INTERVAL` | `30` | 心跳检查间隔 |
| `HOST` | `0.0.0.0` | 服务监听地址 |
| `PORT` | `8000` | 服务端口 |

### 2.4 开发工具

- **IDE**：VS Code + Python扩展
- **API文档**：自动生成 `http://localhost:8000/docs` (Swagger UI)
- **数据库工具**：DB Browser for SQLite
- **WebSocket调试**：wscat / Chrome DevTools

---

## 三、核心功能模块详细设计

### 3.1 智能体注册与管理模块

**数据结构**：

```python
class Agent(BaseModel):
    agent_id: str              # UUID唯一标识
    name: str                  # 显示名称
    capabilities: list[str]    # 能力标签 ["python", "nlp"]
    endpoint: str | None       # Webhook回调
    status: AgentStatus        # online/offline/busy/error
    metadata: dict             # 自定义元数据
    current_task_count: int    # 当前任务数（负载）
    registered_at: datetime
    last_heartbeat: datetime
```

**核心算法 - 能力匹配与负载均衡**：

```python
def find_candidates(required_capabilities, exclude_ids):
    """
    候选智能体评分算法：
    - 每个必需能力匹配: +10分
    - 在线状态: +5分
    - 负载因子: max(0, 5 - current_task_count)
    - 不满足任何必需能力: 排除
    - 按总分降序排序
    """
```

**心跳超时检测**：后台定时任务，超过60秒无心跳自动标记为OFFLINE并推送事件。

### 3.2 黑板核心模块

**数据结构**：

```python
class KnowledgeEntry(BaseModel):
    entry_id: str
    topic: str              # 主题分类
    content: Any            # 任意JSON内容
    author_id: str          # 发布者
    version: int            # 乐观锁版本号
    tags: list[str]
    priority: Priority
    confidence: float       # 置信度（冲突投票用）
    created_at/updated_at: datetime
```

**核心算法 - 乐观锁版本控制**：

```python
async def update_entry(entry_id, author_id, update):
    entry = await storage.get_entry(entry_id)
    if update.version != entry.version:
        raise VersionConflictError(current_version=entry.version)
    entry.version += 1
    # ...更新字段
    await storage.save_entry(entry)
```

**条目合并算法**（解决意见冲突）：
- 收集所有冲突条目的内容
- 计算加权平均置信度
- 合并标签（去重）
- 取最高优先级

### 3.3 任务协调模块

**数据结构**：

```python
class Task(BaseModel):
    task_id: str
    title: str
    required_capabilities: list[str]
    priority: Priority
    status: TaskStatus      # pending/assigned/in_progress/review/done/blocked/failed
    assignee_id: str | None
    dependencies: list[str] # 依赖的task_id列表
    result: Any | None
    deadline: datetime | None
```

**核心算法 - 自动分配**：

```python
async def auto_assign(task_id):
    task = await get_task(task_id)
    if task.status != PENDING: return None
    if not await check_dependencies_ready(task): return None

    candidates = await agent_registry.find_candidates(
        task.required_capabilities
    )  # 已按匹配度+负载排序

    if candidates:
        return await assign_task(task_id, candidates[0].agent_id)
```

**循环依赖检测 - DFS算法**：

```python
async def detect_circular_dependencies():
    graph = {t.task_id: t.dependencies for t in tasks}
    color = {node: WHITE for node in graph}  # WHITE=未访问, GRAY=访问中, BLACK=已完成

    async def dfs(node):
        color[node] = GRAY
        for dep in graph.get(node, []):
            if color[dep] == GRAY:
                raise CircularDependencyError()  # 回边检测到循环
            if color[dep] == WHITE:
                await dfs(dep)
        color[node] = BLACK

    for node in graph:
        if color[node] == WHITE:
            await dfs(node)
```

**依赖解锁机制**：任务完成时扫描所有依赖此任务的下游任务，若依赖全部完成则触发自动分配。

### 3.4 冲突解决模块

**冲突类型与检测策略**：

| 类型 | 检测方式 | 解决策略 | 算法 |
|------|---------|---------|------|
| 写冲突 | 版本号不匹配 | merge | 刷新版本，重试机制 |
| 意见冲突 | 同主题多作者矛盾 | vote | 置信度加权投票+合并 |
| 任务分配冲突 | 智能体过载(≥3任务) | priority | 优先级排队 |
| 资源冲突 | 互斥资源争抢 | priority | 先到先得/优先级 |

**冲突解决策略链**：

```python
async def resolve_conflict(conflict_id, strategy=None):
    if strategy is None:
        strategy = select_strategy(conflict)  # 按类型选择

    # 策略链：merge → priority → vote → escalate
    resolution = await apply_strategy(conflict, strategy)

    # escalate不覆盖状态为resolved
    if strategy != "escalate":
        conflict.status = RESOLVED
```

**置信度投票算法**：
```python
# 找出置信度最高的条目作为胜出者
winner = max(entries, key=lambda e: e.confidence)
# 合并为新条目，保留所有内容
merged = await blackboard.merge_entries(entry_ids, topic, resolved_by)
```

### 3.5 动态协作规则引擎

**数据结构**：

```python
class CollaborationRule(BaseModel):
    rule_id: str
    name: str
    trigger: RuleTrigger    # on_task_created, on_conflict_detected...
    condition: dict         # 条件表达式
    action: RuleAction      # auto_assign, escalate, notify...
    action_params: dict
    enabled: bool
    priority: int           # 执行优先级（降序）
```

**条件匹配引擎**：

支持9种操作符：`eq`, `ne`, `gt`, `lt`, `gte`, `lte`, `in`, `nin`, `contains`

支持复合条件（AND/OR嵌套）：

```json
{
    "and": [
        {"field": "priority", "operator": "eq", "value": "high"},
        {"field": "urgent", "operator": "eq", "value": true}
    ]
}
```

支持点号路径访问嵌套字段：`{"field": "data.priority", "operator": "gt", "value": 5}`

**规则执行流程**：

```
事件触发 → evaluate_rules(trigger, event_data)
         → 从缓存获取匹配trigger的规则（按priority降序）
         → 逐条匹配condition
         → 执行匹配规则的action
         → 返回执行结果列表
```

**热加载机制**：规则CRUD操作发布事件 → 触发缓存重新加载 → 新规则即时生效。

**默认种子规则**：
1. 高优先级任务自动分配 (priority=10)
2. 严重任务自动分配 (priority=20)
3. 冲突自动升级 (priority=5)
4. 新智能体注册通知 (priority=1)

### 3.6 事件总线

**设计模式**：发布-订阅模式

**核心数据结构**：
```python
_subscribers: dict[str, list[asyncio.Queue[Event]]]  # 频道→订阅队列
_handlers: dict[str, list[Callable]]                  # 事件类型→处理器
```

**频道映射策略**：
- 精确匹配频道：`event_type`（如 `task.created`）
- 实体专属频道：`agent:{agent_id}`
- 聚合频道：`tasks`, `entries`, `conflicts`, `rules`
- 主题频道：`entries:{topic}`

**背压处理**：队列满时丢弃事件，避免阻塞发布者。

---

## 四、潜在性能优化方向及实施步骤

### 4.1 高优先级优化

#### 优化1：数据库迁移至PostgreSQL

**现状**：SQLite单写锁限制写入吞吐量~20/s

**实施步骤**：
1. 安装PostgreSQL 16+
2. 实现PostgreSQLStorage类（实现StorageBackend接口）
3. 配置连接池（asyncpg）
4. 添加数据库迁移脚本（Alembic）
5. 切换`DATABASE_URL`环境变量
6. 性能对比测试

**预期收益**：写入吞吐量提升至500+ req/s，支持高并发场景

#### 优化2：引入Redis缓存与分布式事件总线

**现状**：内存事件总线不支持多实例部署

**实施步骤**：
1. 部署Redis 7+
2. 实现RedisEventBus（pub/sub + Streams）
3. 热点数据（智能体列表、规则）缓存
4. 会话状态共享
5. WebSocket横向扩展（Redis适配器）

**预期收益**：支持多实例部署，事件总线吞吐量提升10倍+

#### 优化3：数据库索引优化

**实施步骤**：
```sql
-- 智能体状态查询
CREATE INDEX idx_agents_status ON agents(status);
-- 任务多条件查询
CREATE INDEX idx_tasks_status_priority ON tasks(status, priority);
-- 条目主题+时间范围查询
CREATE INDEX idx_entries_topic_updated ON knowledge_entries(topic, updated_at DESC);
-- 冲突状态查询
CREATE INDEX idx_conflicts_status ON conflicts(status);
```

**预期收益**：查询性能提升5-10倍

### 4.2 中优先级优化

#### 优化4：批量写入与事务优化

**实施方案**：
- 批量API：`POST /api/entries/batch` 一次写入多条
- WAL模式：`PRAGMA journal_mode=WAL`（SQLite）
- 连接池复用

**预期收益**：批量写入吞吐量提升3-5倍

#### 优化5：心跳机制优化

**现状**：固定间隔轮询（30秒）

**优化方案**：
- 改用WebSocket心跳检测（客户端ping/pong）
- 时间轮算法管理超时（O(1)复杂度）
- 自适应心跳间隔（根据智能体活跃度动态调整）

**预期收益**：CPU占用降低，心跳检测延迟降低至秒级

#### 优化6：规则引擎性能优化

**实施方案**：
- 规则条件预编译（将条件表达式编译为AST）
- 规则索引（按trigger+字段建立索引，减少匹配次数）
- 规则执行结果缓存

**预期收益**：规则评估延迟从ms级降至μs级

### 4.3 低优先级优化

#### 优化7：监控与可观测性

- 集成Prometheus指标暴露（`/metrics`）
- Grafana可视化仪表盘
- 分布式链路追踪（OpenTelemetry）
- 结构化日志（JSON格式）

#### 优化8：安全加固

- JWT认证与RBAC权限控制
- API速率限制（防止滥用）
- WebSocket消息大小限制
- 输入验证与SQL注入防护

#### 优化9：高可用部署

- 多实例+Nginx负载均衡
- 数据库主从复制
- 健康检查与自动故障转移
- 蓝绿部署/滚动更新

---

## 五、测试报告摘要

### 5.1 单元测试与集成测试

- **测试数量**：34个测试用例
- **通过率**：100%
- **覆盖模块**：智能体管理、黑板核心、任务协调、冲突解决、规则引擎、完整集成流程

详细测试用例见 `tests/` 目录。

### 5.2 性能测试结果

| 指标 | 目标 | 实测 | 达标 |
|------|------|------|------|
| API P95响应时间 | < 100ms | < 45ms | ✅ |
| WebSocket推送延迟 | < 50ms | 24.69ms | ✅ |
| 100并发成功率 | 100% | 100% | ✅ |
| 系统吞吐量 | - | 130 req/s | - |
| 内存占用 | 低 | < 1MB | ✅ |

详细报告见 `reports/performance_test_report.md`。

---

## 六、技术文档索引

| 文档 | 路径 | 说明 |
|------|------|------|
| 架构设计文档 | `ARCHITECTURE.md` | 系统架构、模块设计、数据结构 |
| 反思分析报告 | `reports/reflective_analysis.md` | 技术选型评估、功能分析、性能分析、UX分析 |
| 性能测试报告 | `reports/performance_test_report.md` | 量化性能测试数据 |
| API文档 | `http://localhost:8000/docs` | 自动生成的OpenAPI文档 |
| 测试代码 | `tests/` | 34个自动化测试用例 |
| 源代码 | `app/` | 6个核心模块+API层 |

---

## 七、总结

本方案基于黑板模式构建了一个功能完整、性能优异的多智能体动态协作系统。系统实现了智能体注册管理、实时信息共享、智能任务分配、冲突自动解决和动态规则配置五大核心功能，所有功能均通过自动化测试验证。性能测试表明系统响应时间、并发能力和实时性均达到或超过设计目标。

通过存储抽象层和事件总线的设计，系统具备良好的可扩展性，可平滑迁移至PostgreSQL+Redis的生产级架构。性能优化路径清晰，从数据库迁移到规则引擎优化，为系统演进提供了明确的实施蓝图。
