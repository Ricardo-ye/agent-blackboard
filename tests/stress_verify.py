"""并发一致性压力测试：验证高并发下数据不错乱、不丢失"""
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
PORT = 8241
BASE = f"http://127.0.0.1:{PORT}"
API_KEY = "stress-key"

checks = []


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


def req(method, path, body=None, timeout=30):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("X-API-Key", API_KEY)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, None


def check(name, ok, detail):
    checks.append((name, ok, detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + f"  |  {detail}")


async def main_async():
    import httpx
    limits = httpx.Limits(max_connections=100, max_keepalive_connections=50)
    headers = {"X-API-Key": API_KEY}
    async with httpx.AsyncClient(limits=limits, timeout=30.0, headers=headers,
                                 base_url=BASE) as cli:
        r = await cli.post("/api/agents", json={"name": "Stress", "capabilities": ["x"]})
        aid = r.json()["agent_id"]

        print("\n[T1] 50 并发创建条目（唯一性检查）")
        async def mk(i):
            r = await cli.post(f"/api/entries?author_id={aid}",
                               json={"topic": "stress", "content": f"payload-{i}"})
            return r.status_code, (r.json().get("entry_id") if r.status_code == 201 else None)

        res = await asyncio.gather(*[mk(i) for i in range(50)])
        ok = [e for s, e in res if s == 201]
        check("50 条并发创建全部成功", len(ok) == 50, f"成功 {len(ok)}/50")
        check("entry_id 全局唯一", len(set(ok)) == len(ok), f"唯一 {len(set(ok))}/{len(ok)}")

        r = await cli.get("/api/entries?topic=stress")
        entries = r.json()
        check("落库条数一致", len(entries) == 50, f"实际 {len(entries)} 条")

        print("\n[T2] 同一 entry 的乐观锁并发更新（应只有 1 个成功）")
        eid = entries[0]["entry_id"]
        async def bump(_):
            r = await cli.put(f"/api/entries/{eid}?author_id={aid}",
                              json={"content": "conflict-test", "version": 1})
            return r.status_code
        codes = await asyncio.gather(*[bump(i) for i in range(10)])
        n200 = sum(1 for c in codes if c == 200)
        n409 = sum(1 for c in codes if c == 409)
        check("乐观锁防并发覆盖", n200 == 1 and n409 == 9,
              f"200×{n200}, 409×{n409}（期望 1 / 9）")

        print("\n[T3] 并发分配任务（负载计数一致性）")
        r = await cli.post("/api/agents", json={"name": "LoadTarget", "capabilities": ["y"]})
        lid = r.json()["agent_id"]
        tids = []
        for i in range(40):
            r = await cli.post("/api/tasks", json={"title": f"ST{i}"})
            tids.append(r.json()["task_id"])

        async def assign(tid):
            r = await cli.post(f"/api/tasks/{tid}/assign?assignee_id={lid}")
            return r.status_code
        codes = await asyncio.gather(*[assign(t) for t in tids])
        r = await cli.get(f"/api/agents/{lid}")
        cnt = r.json()["current_task_count"]
        check("40 并发分配计数准确", cnt == 40 and sum(1 for c in codes if c == 200) == 40,
              f"count={cnt}, 成功 {sum(1 for c in codes if c == 200)}/40")

        print("\n[T4] 并发同主题写（冲突检测幂等）")
        r = await cli.post("/api/agents", json={"name": "Other", "capabilities": ["z"]})
        oid = r.json()["agent_id"]
        async def mk2(i):
            author = aid if i % 2 == 0 else oid
            r = await cli.post(f"/api/entries?author_id={author}",
                               json={"topic": "conflict-storm", "content": f"v{i}"})
            return r.status_code
        await asyncio.gather(*[mk2(i) for i in range(20)])

        r = await cli.get("/api/conflicts")
        allc = r.json()
        opc = [c for c in allc if c["conflict_type"] == "opinion_conflict"
               and c.get("context", {}).get("topic") == "conflict-storm"]
        check("同主题冲突不爆炸", len(opc) <= 2,
              f"opinion 冲突 {len(opc)} 条（20 次写入，期望 ≤2）")

        print("\n[T5] 混合读写并发（事件总线稳定性）")
        async def mixed(i):
            if i % 3 == 0:
                r = await cli.post("/api/tasks", json={"title": f"M{i}"})
            elif i % 3 == 1:
                r = await cli.get("/api/entries")
            else:
                r = await cli.get("/api/stats")
            return r.status_code
        codes = await asyncio.gather(*[mixed(i) for i in range(90)])
        okn = sum(1 for c in codes if c < 500)
        check("混合读写无 5xx", okn == 90, f"非5xx {okn}/90")

        print("\n[T6] 最终统计一致性")
        r = await cli.get("/api/stats")
        st = r.json()
        check("统计接口可用且自洽",
              st["total_tasks"] >= st["pending_tasks"] + st["completed_tasks"],
              json.dumps(st, ensure_ascii=False))


def main():
    tmpdir = tempfile.mkdtemp(prefix="stress-")
    env = dict(os.environ)
    env["BLACKBOARD_DB_PATH"] = os.path.join(tmpdir, "stress.db")
    env["BLACKBOARD_API_KEY"] = API_KEY
    env["PYTHONPATH"] = ROOT
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        if not wait_ready():
            print("服务未就绪")
            proc.terminate()
            print(proc.communicate(timeout=10)[0].decode(errors="replace")[:2000])
            return 1
        print("=" * 66)
        print("并发一致性压力测试")
        print("=" * 66)
        asyncio.run(main_async())
        n_fail = sum(1 for _, ok, _ in checks if not ok)
        print("\n" + "=" * 66)
        print(f"{len(checks) - n_fail} PASS / {n_fail} FAIL")
        print("=" * 66)
        return 1 if n_fail else 0
    finally:
        proc.terminate()
        try:
            out = proc.communicate(timeout=10)[0].decode(errors="replace")
            errs = [l for l in out.splitlines()
                    if "Traceback" in l or "ERROR" in l or "Exception" in l]
            if errs:
                print("\n服务端异常日志：")
                for l in errs[:15]:
                    print("   ", l)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
