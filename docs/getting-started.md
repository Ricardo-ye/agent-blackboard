# 快速开始

Agent Blackboard 是一个基于黑板模式的多智能体协作参考实现。它把智能体能力、任务、共享知识、
冲突和规则放到同一套可观察的协作闭环中。

## 方式一：Docker Compose

```bash
docker compose up --build
```

打开 <http://localhost:8000/ui/> 查看控制台，或访问 <http://localhost:8000/docs> 浏览 API。
数据库保存在命名卷 `blackboard-data` 中。停止服务不会删除数据；如需从头演示，执行
`docker compose down -v`。

## 方式二：本地 Python

要求 Python 3.11 或更高版本。

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8000
```

## 第一个协作任务

保持服务运行，在另一个终端执行：

```bash
python examples/quickstart.py
```

脚本会注册一个具有 `python` 能力的智能体、创建一个高优先级任务并发布一条共享知识。随后打开
控制台的“任务”和“黑板条目”页面观察结果。

## 演示数据

```bash
python scripts/seed_demo.py
```

该命令只新增数据，不会清理现有条目。需要隔离演示时，先设置 `BLACKBOARD_DB_PATH` 指向新的
SQLite 文件。
