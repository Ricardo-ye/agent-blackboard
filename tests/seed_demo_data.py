"""演示数据灌入脚本（命令行版）

用法：
    1) 先启动服务：  uvicorn main:app --port 8000
    2) 再执行脚本：  .\\python\\python.exe tests\\seed_demo_data.py

也可指定其它地址：
    .\\python\\python.exe tests\\seed_demo_data.py http://127.0.0.1:9000

若服务端启用了 BLACKBOARD_API_KEY，通过环境变量传入：
    set BLACKBOARD_API_KEY=xxx && .\\python\\python.exe tests\\seed_demo_data.py

说明：只做新增，不删除任何既有数据；每一步失败都会继续，最后汇总。
"""
from __future__ import annotations

import os
import sys

import httpx

DEFAULT_BASE = "http://127.0.0.1:8000"

AGENTS = [
    {"name": "架构师Agent", "capabilities": ["architecture", "design"]},
    {"name": "编码Agent", "capabilities": ["python", "code_gen"]},
    {"name": "检索Agent", "capabilities": ["search", "nlp"]},
    {"name": "测试Agent", "capabilities": ["testing", "qa"]},
    {"name": "评审Agent", "capabilities": ["review", "design"]},
]


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else os.getenv("BLACKBOARD_BASE_URL", DEFAULT_BASE)).rstrip("/")
    key = os.getenv("BLACKBOARD_API_KEY", "")
    headers = {"X-API-Key": key} if key else {}

    stats = {"agents": 0, "entries": 0, "tasks": 0, "conflicts": 0, "errors": []}

    def guard(label, fn):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001
            stats["errors"].append(f"{label}: {e}")
            return None

    with httpx.Client(base_url=base, headers=headers, timeout=20.0) as client:
        # 连通性检查
        try:
            root = client.get("/")
            root.raise_for_status()
        except Exception as e:  # noqa: BLE001
            print(f"无法连接 {base} —— {e}")
            print("请先启动服务：uvicorn main:app --port 8000")
            return 1
        print(f"已连接：{root.json().get('name')} @ {base}")

        # 1. 智能体
        created = []
        for spec in AGENTS:
            r = guard(f"注册 {spec['name']}", lambda s=spec: client.post("/api/agents", json=s))
            if r is not None and r.status_code == 201:
                created.append(r.json())
                stats["agents"] += 1
                print(f"  [智能体] {spec['name']}")
        if not created:
            print("没有可用智能体，后续步骤跳过")
            return 1 if stats["errors"] else 0

        def nid(name):
            return next((a["agent_id"] for a in created if a["name"] == name), created[0]["agent_id"])

        arch, coder = nid("架构师Agent"), nid("编码Agent")
        searcher, tester, reviewer = nid("检索Agent"), nid("测试Agent"), nid("评审Agent")

        # 2. 条目（architecture 主题下刻意写入两种不同观点）
        ENTRIES = [
            (arch, "architecture", {"pattern": "blackboard", "note": "以黑板为中枢"}, ["架构"], "high", 1.0),
            (reviewer, "architecture", {"pattern": "message_bus", "note": "应改为消息总线"}, ["架构", "争议"], "high", 1.0),
            (coder, "implementation", {"stack": ["FastAPI", "SQLite"], "optimistic_lock": True}, ["实现"], "normal", 1.0),
            (searcher, "research", {"papers": 3, "conclusion": "黑板模式适合弱耦合场景"}, ["调研"], "normal", 0.8),
            (tester, "test-plan", {"cases": ["并发写入一致性", "依赖解锁"], "coverage": "78%"}, ["测试"], "normal", 1.0),
            (arch, "risk", {"risks": ["单写者瓶颈", "心跳误判离线"]}, ["风险"], "critical", 1.0),
        ]
        for author, topic, content, tags, prio, conf in ENTRIES:
            def publish(a=author, t=topic, ct=content, g=tags, p=prio, f=conf):
                return client.post("/api/entries", params={"author_id": a},
                                   json={"topic": t, "content": ct, "tags": g,
                                         "priority": p, "confidence": f})

            r = guard(f"发布 {topic}", publish)
            if r is not None and r.status_code == 201:
                stats["entries"] += 1
                print(f"  [条目] {topic}")

        # 3. 任务链
        def new_task(title, desc, caps, prio, deps=None):
            body = {"title": title, "description": desc, "required_capabilities": caps,
                    "priority": prio, "dependencies": deps or []}
            r = guard(f"创建任务 {title}", lambda b=dict(body): client.post("/api/tasks", json=b))
            return r.json() if (r is not None and r.status_code == 201) else None

        t1 = new_task("确定系统架构方案", "对比黑板模式与消息总线", ["architecture"], "high")
        t2 = new_task("实现黑板核心读写", "条目 CRUD + 乐观锁", ["python"], "normal",
                      [t1["task_id"]] if t1 else [])
        t3 = new_task("调研多智能体协作范式", "", ["search"], "critical")
        t4 = new_task("编写并发一致性测试", "", ["testing"], "normal",
                      [t["task_id"] for t in (t2, t3) if t])
        t5 = new_task("架构方案评审", "", ["review"], "normal", [t1["task_id"]] if t1 else [])
        t6 = new_task("量子加速算子优化", "故意声明无人具备的能力", ["quantum"], "low")
        stats["tasks"] += sum(1 for t in (t1, t2, t3, t4, t5, t6) if t)
        print(f"  [任务] 已创建 {stats['tasks']} 个")

        # 4. 推进：完成 t1 解锁下游，再自动分配
        if t1:
            guard("完成架构任务", lambda: client.patch(
                f"/api/tasks/{t1['task_id']}", params={"updater_id": arch},
                json={"status": "done", "result": {"chosen": "blackboard"}}))
        if t2:
            r = guard("自动分配编码任务", lambda: client.post(f"/api/tasks/{t2['task_id']}/auto-assign"))
            if r is not None and r.status_code == 200:
                print(f"  [分配] 编码任务 -> {r.json().get('assignee_id', '')[:8]}")
        if t3:
            guard("推进调研任务", lambda: client.patch(
                f"/api/tasks/{t3['task_id']}", params={"updater_id": searcher},
                json={"status": "in_progress"}))
        if t5:
            guard("推进评审任务", lambda: client.patch(
                f"/api/tasks/{t5['task_id']}", params={"updater_id": reviewer},
                json={"status": "in_progress"}))

        # 5. 触发意见冲突检测
        r = guard("检测意见冲突",
                  lambda: client.post("/api/conflicts/detect/opinion", params={"topic": "architecture"}))
        if r is not None and r.status_code == 200 and r.json().get("conflict"):
            stats["conflicts"] += 1
            print("  [冲突] architecture 主题检测到意见冲突")

        try:
            s = client.get("/api/stats").json()
        except Exception:  # noqa: BLE001
            s = {}

    print("-" * 56)
    print(f"完成：智能体 +{stats['agents']} / 条目 +{stats['entries']} / "
          f"任务 +{stats['tasks']} / 冲突 +{stats['conflicts']}")
    print(f"当前系统：{s}")
    if stats["errors"]:
        print(f"失败 {len(stats['errors'])} 项：")
        for e in stats["errors"][:8]:
            print(f"  - {e}")
    print(f"\n打开控制台查看：{base}/ui/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
