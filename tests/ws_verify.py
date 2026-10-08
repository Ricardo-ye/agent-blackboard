"""WebSocket 链路验证：自启服务 + 订阅 + 实时推送，全流程自动化"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8211
BASE = f"http://127.0.0.1:{PORT}"
API_KEY = "ws-test-key"


def wait_ready(timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/", timeout=3) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def post(path, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-API-Key", API_KEY)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, None


async def ws_check():
    import websockets
    results = []
    async with websockets.connect(f"ws://127.0.0.1:{PORT}/ws") as ws:
        results.append(("WebSocket 连接", True, ""))
        await ws.send(json.dumps({
            "action": "subscribe",
            "channels": ["agent.registered", "task.created", "entry.created"],
        }))
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        ack = json.loads(raw)
        results.append(("订阅确认", ack.get("event") == "subscribed", raw[:120]))

        # 触发 agent.registered
        s, _ = post("/api/agents", {"name": "WSAgent", "capabilities": ["python"]})
        results.append(("触发事件 status=201", s == 201, f"status={s}"))
        raw = await asyncio.wait_for(ws.recv(), timeout=8)
        msg = json.loads(raw)
        results.append(("收到实时推送", msg.get("event") == "agent.registered",
                        json.dumps(msg, ensure_ascii=False)[:150]))
        results.append(("实时推送携带关联 ID", bool(msg.get("trace_id")),
                        f"trace_id={msg.get('trace_id')}"))

        # 触发 task.created
        s, _ = post("/api/tasks", {"title": "WS Task"})
        raw = await asyncio.wait_for(ws.recv(), timeout=8)
        msg = json.loads(raw)
        results.append(("第二条推送到达", msg.get("event") == "task.created",
                        json.dumps(msg, ensure_ascii=False)[:150]))

        # 心跳保活检测
        await ws.ping()
        results.append(("Ping/Pong 保活", True, ""))

        # 未知 action 应有明确错误响应（不静默）
        await ws.send(json.dumps({"action": "unsubscribe"}))
        raw = await asyncio.wait_for(ws.recv(), timeout=5)
        msg = json.loads(raw)
        results.append(("未知 action 返回错误提示", msg.get("event") == "error", raw[:120]))
    return results


def main():
    tmpdir = tempfile.mkdtemp(prefix="ws-")
    env = dict(os.environ)
    env["BLACKBOARD_DB_PATH"] = os.path.join(tmpdir, "ws.db")
    env["BLACKBOARD_API_KEY"] = API_KEY
    env["PYTHONPATH"] = ROOT
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", str(PORT), "--log-level", "info"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        if not wait_ready():
            print("服务未就绪")
            proc.terminate()
            print(proc.communicate(timeout=10)[0].decode(errors="replace")[:2000])
            return 1

        print("=" * 60)
        print("WebSocket 链路验证")
        print("=" * 60)
        results = asyncio.run(ws_check())
        failed = 0
        for name, ok, detail in results:
            print(("  PASS  " if ok else "  FAIL  ") + name + (f"  |  {detail}" if detail else ""))
            if not ok:
                failed += 1
        print("-" * 60)
        print(f"{len(results) - failed} PASS / {failed} FAIL")
        return 1 if failed else 0
    except Exception as e:
        import traceback
        traceback.print_exc()
        return 1
    finally:
        proc.terminate()
        try:
            out = proc.communicate(timeout=10)[0].decode(errors="replace")
            errs = [l for l in out.splitlines()
                    if "Traceback" in l or "WebSocket error" in l or "ERROR" in l]
            if errs:
                print("\n服务端异常日志：")
                for l in errs[:10]:
                    print("   ", l)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
