"""UI 契约探查 - 确认前端将要依赖的每个接口的真实字段与状态码。

自启自停 uvicorn（临时库 + 独立端口），逐条打印响应结构，
用于在写前端之前锁定真实契约，避免凭源码想当然。
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / "python" / "python.exe")
PORT = 8251
BASE = f"http://127.0.0.1:{PORT}"


def _free_port(preferred: int) -> int:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def start_server(db_path: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["BLACKBOARD_DB_PATH"] = db_path
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(PORT),
         "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    for _ in range(120):
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                return proc
        except OSError:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"server exited early:\n{out}")
            time.sleep(0.25)
    proc.kill()
    raise RuntimeError("server did not become ready")


def show(label: str, resp: httpx.Response) -> None:
    body = resp.text
    if len(body) > 420:
        body = body[:420] + " ...<truncated>"
    print(f"\n--- {label}\n    {resp.request.method} {resp.request.url} -> {resp.status_code}\n    {body}")


async def main() -> int:
    global PORT
    PORT = _free_port(PORT)
    global BASE
    BASE = f"http://127.0.0.1:{PORT}"

    tmpdir = tempfile.mkdtemp(prefix="ui_probe_")
    db = str(Path(tmpdir) / "probe.db")
    proc = start_server(db)
    ok = True
    try:
        async with httpx.AsyncClient(base_url=BASE, timeout=15.0) as c:
            print("=" * 70)
            print(f"UI 契约探查 @ {BASE}  (db={db})")
            print("=" * 70)

            show("根端点", await c.get("/"))
            show("统计", await c.get("/api/stats"))

            # ---- 智能体 ----
            show("注册智能体", await c.post("/api/agents", json={
                "name": "CodeAgent", "capabilities": ["python", "code_gen"]}))
            r = await c.post("/api/agents", json={
                "name": "NLPAgent", "capabilities": ["nlp", "python"]})
            nlp_id = r.json()["agent_id"]
            show("注册第二个智能体", r)
            show("智能体列表", await c.get("/api/agents"))
            show("心跳", await c.post(f"/api/agents/{nlp_id}/heartbeat"))
            show("改状态", await c.patch(f"/api/agents/{nlp_id}/status", params={"status": "busy"}))

            # ---- 条目 ----
            r = await c.post("/api/entries", params={"author_id": nlp_id}, json={
                "topic": "design", "content": {"pattern": "blackboard"}, "tags": ["arch"]})
            show("创建条目", r)
            entry = r.json()
            eid, ever = entry["entry_id"], entry["version"]

            show("条目列表", await c.get("/api/entries"))
            show("条目详情", await c.get(f"/api/entries/{eid}"))
            show("乐观锁更新(版本正确)", await c.put(
                f"/api/entries/{eid}", params={"author_id": nlp_id},
                json={"content": {"pattern": "blackboard-v2"}, "version": ever}))
            show("乐观锁更新(版本过期)", await c.put(
                f"/api/entries/{eid}", params={"author_id": nlp_id},
                json={"content": {"pattern": "stale"}, "version": ever}))

            # 意见冲突：同主题不同作者不同内容
            r2 = await c.post("/api/entries", params={"author_id": nlp_id}, json={
                "topic": "conflict_demo", "content": {"answer": "A"}})
            r3 = await c.post("/api/entries", params={"author_id": nlp_id}, json={
                "topic": "conflict_demo", "content": {"answer": "B"}})
            show("同主题第二条", r2)
            show("同主题第三条", r3)
            show("手动检测意见冲突", await c.post("/api/conflicts/detect/opinion",
                                          params={"topic": "conflict_demo"}))
            show("冲突列表", await c.get("/api/conflicts"))

            # 合并
            ids = [r2.json()["entry_id"], r3.json()["entry_id"]]
            show("合并条目", await c.post("/api/entries/merge", params={"author_id": nlp_id},
                                     json={"source_ids": ids, "target_topic": "merged"}))

            # ---- 任务 ----
            r = await c.post("/api/tasks", json={
                "title": "实现黑板同步", "description": "同步逻辑",
                "required_capabilities": ["python"], "priority": "high"})
            show("创建任务(high，会触发自动分配规则)", r)
            t1 = r.json()["task_id"]

            r = await c.post("/api/tasks", json={
                "title": "编写文档", "required_capabilities": ["nlp"], "priority": "normal",
                "dependencies": [t1]})
            show("创建带依赖任务(依赖未完成)", r)
            t2 = r.json()["task_id"]

            show("任务列表", await c.get("/api/tasks"))
            show("按状态过滤", await c.get("/api/tasks", params={"status": "pending"}))
            show("任务详情", await c.get(f"/api/tasks/{t2}"))
            show("自动分配(依赖未就绪->409)", await c.post(f"/api/tasks/{t2}/auto-assign"))
            show("手动分配", await c.post(f"/api/tasks/{t2}/assign", params={"assignee_id": nlp_id}))
            show("更新状态PATCH", await c.patch(f"/api/tasks/{t1}",
                                            params={"updater_id": nlp_id},
                                            json={"status": "done", "result": {"ok": True}}))
            show("依赖解锁后自动分配", await c.post(f"/api/tasks/{t2}/auto-assign"))
            show("智能体任务", await c.get(f"/api/agents/{nlp_id}/tasks"))
            show("循环依赖检测", await c.post("/api/tasks", json={
                "title": "环", "dependencies": [t1, t2], "priority": "low"}))

            # ---- 规则 ----
            show("规则列表(含种子规则)", await c.get("/api/rules"))
            r = await c.post("/api/rules", json={
                "name": "UI演示规则", "trigger": "on_entry_created",
                "condition": {"field": "topic", "operator": "eq", "value": "ui"},
                "action": "notify",
                "action_params": {"channel": "system", "message": "UI 规则命中"}})
            show("创建规则", r)
            rid = r.json()["rule_id"]
            show("更新规则", await c.patch(f"/api/rules/{rid}", json={"enabled": False, "priority": 3}))
            show("规则非法字段(应422)", await c.patch(f"/api/rules/{rid}", json={"rule_id": "hack"}))
            show("删除规则", await c.delete(f"/api/rules/{rid}"))

            # ---- 404 / 边界 ----
            show("不存在的条目(404)", await c.get("/api/entries/nope"))
            show("不存在的任务(404)", await c.get("/api/tasks/nope"))
            show("空名称智能体", await c.post("/api/agents", json={"name": ""}))
            show("无能力智能体自动分配", await c.post("/api/tasks", json={
                "title": "无人可做", "required_capabilities": ["quantum"], "priority": "low"}))

            show("最终统计", await c.get("/api/stats"))

        # ---- WebSocket 契约 ----
        print("\n" + "=" * 70)
        print("WebSocket 契约")
        print("=" * 70)
        import websockets
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws") as ws:
            await ws.send(json.dumps({"action": "ping"}))
            print("  ping ->", await asyncio.wait_for(ws.recv(), timeout=5))
            await ws.send(json.dumps({"action": "subscribe",
                                      "channels": ["task.created", "task.status_changed",
                                                   "conflict.detected", "entry.updated"]}))
            print("  subscribe ->", await asyncio.wait_for(ws.recv(), timeout=5))
            # 触发一个事件验证推送
            async with httpx.AsyncClient(base_url=BASE, timeout=10) as c:
                await c.post("/api/entries", params={"author_id": "probe"}, json={
                    "topic": "ws_probe", "content": {"x": 1}})
            try:
                print("  推送 ->", await asyncio.wait_for(ws.recv(), timeout=6))
            except asyncio.TimeoutError:
                print("  推送 -> (6s 内未收到)")
                ok = False

        print("\n" + "=" * 70)
        print("契约探查完成")
        print("=" * 70)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
