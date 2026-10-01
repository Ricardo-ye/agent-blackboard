# Agent Blackboard（智能体动态协作黑板系统）

基于经典黑板模式（Blackboard Pattern）的多智能体动态协作系统，支持智能体注册管理、实时信息共享、智能任务分配、冲突自动解决和动态协作规则配置。

项目提供 FastAPI 后端、SQLite 异步存储、WebSocket 实时事件流和一套无需构建的 Web 控制台，适合用于多智能体协调机制的原型验证、教学与二次开发。

## 核心功能

| 功能模块 | 说明 |
|---------|------|
| 智能体注册与管理 | 智能体动态注册/注销、心跳检测、能力声明与匹配 |
| 黑板实时共享 | 知识条目CRUD、乐观锁版本控制、WebSocket实时推送 |
| 任务分配与协调 | 基于能力+负载的自动分配、依赖管理、循环依赖检测 |
| 冲突解决 | 写冲突/意见冲突/分配冲突检测，合并/投票/仲裁策略链 |
| 动态规则配置 | 热加载协作规则，支持9种条件操作符与6种执行动作 |

## 快速开始

### 环境要求

- Python 3.11+
- pip

### 安装与启动

```bash
# 建议先创建并激活虚拟环境
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate

# 安装依赖
python -m pip install --upgrade pip
pip install -r requirements.txt

# （可选，生产建议）配置 API 密钥，启用写操作鉴权
export BLACKBOARD_API_KEY="your-secret-key"

# 启动服务
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 访问API文档
# Swagger UI: http://localhost:8000/docs
# ReDoc: http://localhost:8000/redoc
```

### 快速体验

```bash
# 未配置 BLACKBOARD_API_KEY 时无需请求头；已配置时写操作需带 X-API-Key
curl -X POST http://localhost:8000/api/agents \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-secret-key" \
  -d '{"name": "CodeAgent", "capabilities": ["python", "code_gen"]}'
```

```python
import httpx

HEADERS = {"X-API-Key": "your-secret-key"}  # 未启用鉴权时传 {}

# 1. 注册智能体
async with httpx.AsyncClient(headers=HEADERS) as client:
    agent = await client.post("http://localhost:8000/api/agents", json={
        "name": "CodeAgent",
        "capabilities": ["python", "code_gen"]
    })
    agent_id = agent.json()["agent_id"]

    # 2. 创建任务（高优先级会自动分配）
    task = await client.post("http://localhost:8000/api/tasks", json={
        "title": "Implement feature",
        "required_capabilities": ["python"],
        "priority": "high",
        "creator_id": agent_id
    })
    print(task.json()["status"])  # "assigned"

    # 3. 发布知识到黑板
    entry = await client.post(
        f"http://localhost:8000/api/entries?author_id={agent_id}",
        json={"topic": "design", "content": {"pattern": "blackboard"}}
    )
```

## Web 控制台

后端之外还内置了一套**纯前端控制台**（原生 ES Module，零构建、零新增依赖），
服务启动后直接访问即可，无需任何前端工具链。

```bash
# 启动服务后打开
http://localhost:8000/ui/
```

控制台挂在独立前缀 `/ui` 下，与 `/api`、`/ws` 完全隔离，**不影响任何既有后端行为**。

### 页面与对应功能

| 页面 | 路由 | 覆盖的后端能力 |
|------|------|---------------|
| 仪表盘 | `#/dashboard` | `/api/stats` 全局态势、WebSocket 实时事件流、一键灌入演示数据 |
| 智能体 | `#/agents` | 注册 / 心跳 / 状态流转 / 注销、能力标签、负载计数 |
| 黑板条目 | `#/entries` | 条目 CRUD、**乐观锁编辑**（版本冲突提示 409）、多条目合并 |
| 任务 | `#/tasks` | 创建（含依赖选择）、自动/手动分配、状态流转、结果回填、依赖就绪检查 |
| 冲突治理 | `#/conflicts` | 意见冲突检测、四种解决策略（merge/priority/vote/escalate）、终态保护 |
| 协作规则 | `#/rules` | 条件构造器（9 种运算符）、6 种动作、优先级与启用开关（热加载） |

列表页点击条目可进入详情页（`#/agents/{id}` 等），查看详情与操作入口。

### 设计规范

样式集中在 `web/css/` 三个文件，全站只通过 CSS 变量取值：

- `tokens.css` — 设计令牌：色阶、间距刻度（4px 基准）、字号阶梯、圆角、阴影、动效时长、层级
- `base.css` — 重置、应用骨架（侧栏 + 顶栏 + 内容区）、响应式断点（1100 / 860 / 560px）、排版工具类
- `components.css` — 按钮、表单、卡片、表格、徽章、模态、Toast、骨架屏、空态、错误态、分页等

主题支持**深浅双色**，默认跟随系统偏好，右上角 ☀/☾ 可手动切换并记忆；
首帧前由 `index.html` 内联脚本定好主题，避免刷新闪烁。

### 边界态

加载中（骨架屏）、空数据（引导式空态 + 创建入口）、请求失败（错误态 + 重试按钮）、
乐观锁冲突（409 提示当前版本）、终态冲突禁止重复解决，均有对应展示。

### 演示数据

空库时可在仪表盘点「灌入演示数据」一键生成 5 个智能体、6 条条目（含一对观点冲突）、
6 个任务（含依赖链）；也可用命令行版：

```bash
uvicorn main:app --port 8000          # 先起服务
.\python\python.exe tests\seed_demo_data.py
```

演示脚本只做新增、不删数据。若默认库里已有压力测试遗留的批量数据，建议用一个干净的库演示：

```bash
set BLACKBOARD_DB_PATH=data\demo.db   # Windows
export BLACKBOARD_DB_PATH=data/demo.db # macOS / Linux
uvicorn main:app --port 8000
.\python\python.exe tests\seed_demo_data.py
```

### 运行测试

```bash
# 单元测试与集成测试（85 例）
# pytest.ini 已配置 testpaths 与 asyncio 模式，直接跑即可
.\python\python.exe -m pytest -q

# 端到端验证：自动启停服务，跑完 13 组共 27 项断言
.\python\python.exe tests/e2e_verify.py

# WebSocket 全链路：订阅 / 推送 / 保活，共 7 项断言
.\python\python.exe tests/ws_verify.py

# 并发一致性压力：唯一性 / 乐观锁 / 计数 / 冲突幂等，共 8 项断言
.\python\python.exe tests/stress_verify.py

# 运行时健壮性探查：边界值 / SQL 注入 / 404 / 状态机，共 20 项检查
.\python\python.exe tests/runtime_probe.py

# UI 冒烟测试：真实浏览器（Edge/Chrome 无头）逐页加载，收集 JS 报错
.\python\python.exe tests/ui_smoke.py
.\python\python.exe tests/ui_smoke.py --verbose   # 打印各页面渲染文本

# 性能测试：自动启停服务，无需手工准备
.\python\python.exe tests/run_performance.py
```

> 自启脚本各自使用临时数据库与独立端口（E2E 8123 / 性能 8124 /
> WebSocket 8211 / 压力 8241 / 探查 8231 / UI 冒烟 8261），不会污染开发库；
> 也可通过 `BLACKBOARD_BASE_URL` 指向已在运行的服务。

## API概览

> 配置 `BLACKBOARD_API_KEY` 后，标记 🔒 的写操作需携带请求头 `X-API-Key`；未配置时全部放行（开发模式）。

| 方法 | 路径 | 说明 | 鉴权 |
|------|------|------|------|
| POST | `/api/agents` | 注册智能体 | 🔒 |
| GET | `/api/agents` | 列出智能体 | — |
| POST | `/api/agents/{id}/heartbeat` | 发送心跳 | 🔒 |
| DELETE | `/api/agents/{id}` | 注销智能体 | 🔒 |
| POST | `/api/entries?author_id={id}` | 创建知识条目 | 🔒 |
| POST | `/api/entries/batch?author_id={id}` | 批量创建 1–500 条知识条目（单次提交） | 🔒 |
| GET | `/api/entries` | 列出条目（支持topic过滤） | — |
| PUT | `/api/entries/{id}?author_id={id}` | 更新条目（乐观锁） | 🔒 |
| DELETE | `/api/entries/{id}?author_id={id}` | 删除条目（仅作者） | 🔒 |
| POST | `/api/entries/merge?author_id={id}` | 合并多个条目 | 🔒 |
| POST | `/api/tasks` | 创建任务 | 🔒 |
| POST | `/api/tasks/{id}/assign` | 手动分配任务 | 🔒 |
| POST | `/api/tasks/{id}/auto-assign` | 自动分配任务 | 🔒 |
| PATCH | `/api/tasks/{id}?updater_id={id}` | 更新任务状态 | 🔒 |
| POST | `/api/rules` | 创建协作规则 | 🔒 |
| GET | `/api/rules` | 列出规则 | — |
| PATCH | `/api/rules/{id}` | 更新规则（仅白名单字段） | 🔒 |
| POST | `/api/conflicts/{id}/resolve` | 解决冲突 | 🔒 |
| POST | `/api/conflicts/detect/opinion` | 手动触发意见冲突检测 | 🔒 |
| GET | `/api/stats` | 系统统计 | — |
| GET | `/metrics` | Prometheus 文本指标 | — |
| WS | `/ws` | WebSocket实时事件推送 | — |

## 项目结构

```
agent-tree/
├── .github/                     # CI、Issue 与 PR 模板
├── .env.example                 # 环境变量示例（不含真实凭证）
├── CODE_OF_CONDUCT.md           # 社区行为准则
├── CONTRIBUTING.md              # 贡献指南
├── LICENSE                      # MIT 许可证
├── SECURITY.md                  # 安全报告政策
├── ARCHITECTURE.md              # 架构设计文档
├── README.md                    # 本文件
├── requirements.txt             # 依赖列表
├── config.py                    # 系统配置
├── main.py                      # FastAPI入口
├── app/
│   ├── models.py                # Pydantic数据模型
│   ├── storage.py               # 存储抽象层+SQLite实现
│   ├── event_bus.py             # 异步事件总线
│   ├── deps.py                  # API依赖（鉴权）
│   ├── agent_registry.py        # 智能体注册管理
│   ├── blackboard.py            # 黑板核心服务
│   ├── task_coordinator.py      # 任务协调服务
│   ├── conflict_resolver.py     # 冲突解决器
│   ├── rule_engine.py           # 协作规则引擎
│   ├── services.py              # 服务聚合层
│   └── api/                     # API路由
│       ├── agents.py
│       ├── entries.py
│       ├── tasks.py
│       ├── rules.py
│       ├── conflicts.py
│       └── websocket.py
├── tests/                       # 测试
│   ├── conftest.py
│   ├── test_agents.py
│   ├── test_blackboard.py
│   ├── test_tasks.py
│   ├── test_conflicts.py
│   ├── test_rules.py
│   ├── test_integration.py
│   ├── test_regression_fixes.py # 代码审查修复回归测试
│   └── performance_test.py
└── reports/                     # 分析报告
    ├── reflective_analysis.md       # 反思分析报告
    ├── performance_test_report.md   # 性能测试报告
    └── optimal_implementation_plan.md # 最优制作方案
```

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `BLACKBOARD_DB_PATH` | `data/blackboard.db` | SQLite 数据库路径 |
| `BLACKBOARD_API_KEY` | 空（不鉴权） | 写操作鉴权密钥，生产环境务必设置 |
| `CORS_ORIGINS` | 空（`*`） | 允许的跨域来源，逗号分隔；设置后启用 credentials |
| `HEARTBEAT_TIMEOUT` | 60 | 心跳超时秒数 |
| `HEARTBEAT_CHECK_INTERVAL` | 30 | 心跳检查间隔秒数 |
| `WEBSOCKET_MAX_MESSAGE_BYTES` | 65536 | WebSocket 入站消息最大字节数 |
| `WEBSOCKET_MAX_CHANNELS` | 64 | 单连接最大订阅频道数 |
| `HOST` / `PORT` | `0.0.0.0` / `8000` | 服务监听地址 |

## 技术栈

- **后端框架**: FastAPI + Uvicorn (ASGI)
- **数据建模**: Pydantic v2
- **数据库**: SQLite (aiosqlite)，支持抽象层切换PostgreSQL
- **实时通信**: WebSocket + 异步事件总线
- **测试**: pytest + pytest-asyncio + httpx

## 性能指标

> 以下为 2026-09-30 优化后实测（WAL 模式 + 原子计数 + 事务批量写入）。

| 指标 | 修复前 | 修复后 |
|------|-------|-------|
| 单请求 P95（条目创建） | 107 ms | **4.67 ms** |
| 系统吞吐量（100 并发） | 45–90 req/s | **377–380 req/s** |
| 条目写入吞吐 | 47–63 /s | **209–307 /s** |
| 100 并发成功率 | 100% | 100% |
| 内存峰值 | < 1MB | 0.29 MB |
| 单元测试 | 34 passed / 55.6s | **85 passed / 1.71s** |

详细测试结果见 [性能测试报告](reports/performance_test_report.md)。
回归修复覆盖见 `tests/test_regression_fixes.py`、`tests/test_report_optimizations.py` 与
`tests/test_p2_optimizations.py`。

## 文档

- [架构设计文档](ARCHITECTURE.md)
- [反思分析报告](reports/reflective_analysis.md)
- [性能测试报告](reports/performance_test_report.md)
- [最优制作方案](reports/optimal_implementation_plan.md)
- [API文档](http://localhost:8000/docs) (启动后访问)

## 参与贡献

欢迎通过 Issue 和 Pull Request 参与改进。开始前请阅读 [贡献指南](CONTRIBUTING.md)、
[行为准则](CODE_OF_CONDUCT.md) 与 [安全政策](SECURITY.md)。提交前至少运行编译检查和完整测试套件。

## 许可证

本项目基于 [MIT License](LICENSE) 开源。
