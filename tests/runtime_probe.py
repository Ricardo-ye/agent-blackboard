"""运行时健壮性探查：边界值、异常输入、并发压力、资源回收"""
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
PORT = 8231
BASE = f"http://127.0.0.1:{PORT}"
API_KEY = "probe-key"

findings = []


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


def req(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    r.add_header("X-API-Key", API_KEY)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None), raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw), raw
        except Exception:
            return e.code, None, raw


def record(name, status, note):
    findings.append((name, status, note))
    print(f"  [{status}] {name}  |  {note}")


def main():
    tmpdir = tempfile.mkdtemp(prefix="probe-")
    env = dict(os.environ)
    env["BLACKBOARD_DB_PATH"] = os.path.join(tmpdir, "probe.db")
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
            return 1

        print("=" * 66)
        print("运行时健壮性探查")
        print("=" * 66)

        s, a1, _ = req("POST", "/api/agents", {"name": "P1", "capabilities": ["python"]})
        aid = a1["agent_id"]

        print("\n[A] 输入校验边界")
        s, _, raw = req("POST", "/api/agents", {"name": "", "capabilities": []})
        record("空名称智能体", "PASS" if s in (201, 422) else "WARN",
               f"status={s}（201=允许空名，422=校验拒绝）")

        s, _, raw = req("POST", "/api/tasks", {"title": "x" * 10000})
        record("超长标题(10K字符)", "PASS" if s in (201, 422) else "WARN", f"status={s}")

        s, _, raw = req("POST", f"/api/entries?author_id={aid}",
                        {"topic": "t", "content": {"nested": {"deep": [1, 2, {"x": "y"}]}}})
        record("深层嵌套 JSON 内容", "PASS" if s == 201 else "WARN", f"status={s}")

        s, _, raw = req("POST", f"/api/entries?author_id={aid}",
                        {"topic": "t", "confidence": 99999})
        record("越界 confidence(99999)", "PASS" if s in (201, 422) else "WARN",
               f"status={s}（是否钳制待查）")

        s, _, raw = req("POST", f"/api/entries?author_id={aid}",
                        {"topic": "t", "confidence": -5})
        record("负 confidence(-5)", "PASS" if s in (201, 422) else "WARN", f"status={s}")

        s, _, raw = req("POST", "/api/tasks", {"title": "t", "priority": "SUPER"})
        record("非法枚举 priority", "PASS" if s == 422 else "WARN", f"status={s}")

        print("\n[B] SQL 注入与特殊字符")
        inj = "'; DROP TABLE agents;--"
        s, _, _ = req("POST", f"/api/entries?author_id={aid}", {"topic": inj, "content": "x"})
        s2, alist, _ = req("GET", "/api/agents")
        record("topic 注入 SQL", "PASS" if s == 201 and isinstance(alist, list) else "FAIL",
               f"写入status={s}, agents表存活={isinstance(alist, list)}")

        s, _, _ = req("GET", "/api/entries?topic=" + urllib.parse.quote(inj))
        record("注入字符串回读", "PASS" if s == 200 else "WARN", f"status={s}")

        print("\n[C] 不存在的资源")
        s, _, _ = req("GET", "/api/agents/does-not-exist")
        record("GET 不存在智能体", "PASS" if s == 404 else "WARN", f"status={s}")
        s, _, _ = req("GET", "/api/tasks/does-not-exist")
        record("GET 不存在任务", "PASS" if s == 404 else "WARN", f"status={s}")
        s, _, _ = req("PATCH", "/api/tasks/does-not-exist?updater_id=x", {"title": "n"})
        record("PATCH 不存在任务", "PASS" if s == 404 else "WARN", f"status={s}")
        s, _, _ = req("POST", "/api/conflicts/does-not-exist/resolve?strategy=merge")
        record("解决不存在冲突", "PASS" if s == 404 else "WARN", f"status={s}")

        print("\n[D] 依赖环检测")
        s, t1, _ = req("POST", "/api/tasks", {"title": "A"})
        s, t2, _ = req("POST", "/api/tasks", {"title": "B", "dependencies": [t1["task_id"]]})
        # 尝试制造环：让 A 依赖 B —— 但 A 已存在，需通过更新。
        # 当前 API 无「更新依赖」入口，故直接用存储层验证环检测。
        record("依赖环（API 层无法构造）", "INFO",
               "任务依赖仅在创建时设定，无更新入口，环在 API 层不可构造")

        print("\n[E] 并发压力与资源回收")
        import concurrent.futures as cf
        s, ca, _ = req("POST", "/api/agents", {"name": "LoadA", "capabilities": ["python"]})
        cid = ca["agent_id"]
        tids = []
        for i in range(30):
            s, tt, _ = req("POST", "/api/tasks", {"title": f"L{i}"})
            tids.append(tt["task_id"])

        def assign(tid):
            return req("POST", f"/api/tasks/{tid}/assign?assignee_id={cid}")[0]

        with cf.ThreadPoolExecutor(max_workers=15) as ex:
            codes = list(ex.map(assign, tids))
        s, ag, _ = req("GET", f"/api/agents/{cid}")
        cnt = ag.get("current_task_count")
        record("30 任务并发分配计数", "PASS" if cnt == 30 else "FAIL",
               f"count={cnt} 期望 30, 成功码={sum(1 for c in codes if c == 200)}/30")

        print("\n[F] 幂等与重复操作")
        s, tt, _ = req("POST", "/api/tasks", {"title": "Dup"})
        tid_d = tt["task_id"]
        s1, _, _ = req("POST", f"/api/tasks/{tid_d}/assign?assignee_id={cid}")
        s2, r2, _ = req("POST", f"/api/tasks/{tid_d}/assign?assignee_id={cid}")
        s, ag2, _ = req("GET", f"/api/agents/{cid}")
        record("重复分配给同一智能体", "PASS" if s1 == 200 and s2 == 200 else "WARN",
               f"两次status={s1},{s2} 计数={ag2.get('current_task_count')}（幂等应为31）")

        print("\n[G] 状态机边界")
        s, tz, _ = req("POST", "/api/tasks", {"title": "StateTest"})
        tzid = tz["task_id"]
        s1, r1, _ = req("PATCH", f"/api/tasks/{tzid}?updater_id={aid}", {"status": "done"})
        s2, r2, _ = req("PATCH", f"/api/tasks/{tzid}?updater_id={aid}", {"status": "pending"})
        record("已完成→待处理（状态回退）", "PASS" if s1 == 200 else "WARN",
               f"done={s1}, 回退={s2}（{r2.get('status') if isinstance(r2, dict) else r2}）")

        print("\n[H] 心跳恢复")
        s, hb, _ = req("POST", f"/api/agents/{aid}/heartbeat")
        record("心跳接口", "PASS" if s == 200 else "WARN", f"status={s}")
        s, _, _ = req("POST", "/api/agents/ghost/heartbeat")
        record("不存在智能体心跳", "PASS" if s == 404 else "WARN", f"status={s}")

        print("\n[I] 分页与大数据量")
        s, big, _ = req("GET", "/api/entries")
        record("全量条目返回", "PASS" if s == 200 else "WARN",
               f"status={s}, 条数={len(big) if isinstance(big, list) else 'N/A'}"
               "（无分页参数，数据量大时全量返回）")
        s, bigt, _ = req("GET", "/api/tasks")
        record("全量任务返回", "PASS" if s == 200 else "WARN",
               f"status={s}, 条数={len(bigt) if isinstance(bigt, list) else 'N/A'}")

        print("\n[J] 统计一致性")
        s, stats, _ = req("GET", "/api/stats")
        record("统计接口", "PASS" if s == 200 else "WARN", json.dumps(stats, ensure_ascii=False))

    finally:
        proc.terminate()
        try:
            out = proc.communicate(timeout=10)[0].decode(errors="replace")
            errs = [l for l in out.splitlines()
                    if "Traceback" in l or "ERROR" in l or "Exception" in l]
            if errs:
                print("\n服务端异常日志：")
                for l in errs[:20]:
                    print("   ", l)
        except Exception:
            proc.kill()

    print("\n" + "=" * 66)
    n_pass = sum(1 for _, s, _ in findings if s == "PASS")
    n_warn = sum(1 for _, s, _ in findings if s == "WARN")
    n_fail = sum(1 for _, s, _ in findings if s == "FAIL")
    print(f"PASS {n_pass} / WARN {n_warn} / FAIL {n_fail}")
    print("=" * 66)
    return 0


import urllib.parse  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
