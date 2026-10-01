"""端到端验证：真实启动 uvicorn，跑完整业务流并检查修复效果"""
import json
import os
import signal
import subprocess
import sys
import time
import tempfile
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
API_KEY = "e2e-secret-key"

passed, failed = [], []


def check(name, cond, detail=""):
    (passed if cond else failed).append(name)
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  |  {detail}" if detail else ""))


def req(method, path, body=None, key=None, expect=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if key:
        r.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw) if raw else None
        except Exception:
            return e.code, raw


def main():
    tmpdir = tempfile.mkdtemp(prefix="e2e-")
    env = dict(os.environ)
    env["BLACKBOARD_DB_PATH"] = os.path.join(tmpdir, "e2e.db")
    env["BLACKBOARD_API_KEY"] = API_KEY
    env["PYTHONPATH"] = ROOT
    env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(PORT),
         "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        # 等端口就绪（根路径 / 为健康探针）
        ready = False
        for _ in range(60):
            try:
                s, _ = req("GET", "/")
                if s == 200:
                    ready = True
                    break
            except Exception:
                time.sleep(0.5)
        if not ready:
            print("服务启动失败，输出：")
            proc.terminate()
            print(proc.communicate(timeout=10)[0].decode(errors="replace")[:3000])
            sys.exit(1)
        print(f"\n服务已就绪于 {BASE}（API Key 已启用）\n")

        print("[1] 鉴权")
        s, _ = req("GET", "/api/agents")
        check("GET 无需鉴权", s == 200, f"status={s}")
        s, _ = req("POST", "/api/agents", {"name": "A1", "capabilities": ["python"]})
        check("POST 无 Key 被拒", s == 401, f"status={s}")
        s, _ = req("POST", "/api/agents", {"name": "A1", "capabilities": ["python"]}, key="wrong")
        check("POST 错误 Key 被拒", s == 401, f"status={s}")

        print("\n[2] 注册智能体")
        agents = {}
        for name, caps in [("CodeAgent", ["python", "code_gen"]),
                           ("TestAgent", ["python", "testing"])]:
            s, a = req("POST", "/api/agents", {"name": name, "capabilities": caps}, key=API_KEY)
            check(f"注册 {name}", s == 201, f"status={s}")
            agents[name] = a
        a1 = agents["CodeAgent"]["agent_id"]
        a2 = agents["TestAgent"]["agent_id"]

        print("\n[3] 知识条目 + 乐观锁")
        s, e1 = req("POST", f"/api/entries?author_id={a1}",
                    {"topic": "arch", "content": "方案A"}, key=API_KEY)
        check("创建条目", s == 201, f"status={s}")
        s, e2 = req("POST", f"/api/entries?author_id={a2}",
                    {"topic": "arch", "content": "方案B", "confidence": 0.6}, key=API_KEY)
        check("创建第二条目", s == 201, f"status={s}")
        eid = e1["entry_id"]
        s, _ = req("PUT", f"/api/entries/{eid}?author_id={a1}",
                   {"content": "方案A-改", "version": 99}, key=API_KEY)
        check("版本不匹配返回 409", s == 409, f"status={s}")

        print("\n[4] 路由顺序（S6）：/merge 可达")
        s, merged = req("POST", f"/api/entries/merge?author_id={a1}",
                        {"source_ids": [e1["entry_id"], e2["entry_id"]],
                         "target_topic": "arch-merged"}, key=API_KEY)
        check("POST /api/entries/merge 不再 422", s in (200, 201), f"status={s}")

        print("\n[5] 合并幂等（N5）")
        s, merged2 = req("POST", f"/api/entries/merge?author_id={a1}",
                         {"source_ids": [e2["entry_id"], e1["entry_id"]],
                          "target_topic": "arch-merged"}, key=API_KEY)
        same = (merged and merged2
                and merged.get("entry_id") == merged2.get("entry_id"))
        check("重复合并复用同一条目", same,
              f"{str(merged.get('entry_id'))[:8] if merged else None} vs "
              f"{str(merged2.get('entry_id'))[:8] if merged2 else None}")

        print("\n[6] 意见冲突幂等（S3）")
        for i in range(3):
            s, e = req("POST", f"/api/entries?author_id={a1}",
                       {"topic": "topic-x", "content": f"观点{i}"}, key=API_KEY)
        req("POST", f"/api/entries?author_id={a2}",
            {"topic": "topic-x", "content": "异议观点"}, key=API_KEY)
        conflicts = []
        for _ in range(4):
            s, c = req("POST", "/api/conflicts/detect/opinion?topic=topic-x", None, key=API_KEY)
            if c and c.get("conflict"):
                conflicts.append(c["conflict"]["conflict_id"])
        check("重复检测复用同一冲突", len(set(conflicts)) == 1,
              f"unique={len(set(conflicts))} of {len(conflicts)}")

        print("\n[7] 冲突终态保护（S4）")
        cid = conflicts[0]
        s, _ = req("POST", f"/api/conflicts/{cid}/resolve?strategy=vote", None, key=API_KEY)
        check("首次解决成功", s == 200, f"status={s}")
        s, _ = req("POST", f"/api/conflicts/{cid}/resolve?strategy=vote", None, key=API_KEY)
        check("重复解决被拒（409）", s == 409, f"status={s}")

        print("\n[8] 规则字段白名单（S1）")
        s, rule = req("POST", "/api/rules",
                      {"name": "R1", "trigger": "on_task_created",
                       "action": "auto_assign", "priority": 5}, key=API_KEY)
        check("创建规则", s == 201, f"status={s}")
        rid = rule["rule_id"]
        s, _ = req("PATCH", f"/api/rules/{rid}", {"rule_id": "hacked"}, key=API_KEY)
        check("非法字段被拒（422）", s == 422, f"status={s}")
        s, _ = req("PATCH", f"/api/rules/{rid}", {"priority": "not-an-int"}, key=API_KEY)
        check("非法类型被拒（422）", s == 422, f"status={s}")
        s, rules = req("GET", "/api/rules")
        check("规则列表仍可读（未污染）", s == 200 and isinstance(rules, list), f"status={s}")
        s, st = req("GET", "/api/stats")
        check("统计接口仍可读", s == 200, f"status={s}")

        print("\n[9] 任务依赖 + 批量检查（N4）")
        s, t1 = req("POST", "/api/tasks", {"title": "T1", "creator_id": a1}, key=API_KEY)
        s, t2 = req("POST", "/api/tasks", {"title": "T2", "creator_id": a1}, key=API_KEY)
        s, t3 = req("POST", "/api/tasks",
                    {"title": "T3", "creator_id": a1,
                     "dependencies": [t1["task_id"], t2["task_id"]]}, key=API_KEY)
        check("创建带依赖任务", s == 201, f"status={s}")
        s, _ = req("PATCH", f"/api/tasks/{t1['task_id']}?updater_id={a1}",
                   {"status": "done"}, key=API_KEY)
        check("完成依赖 T1", s == 200, f"status={s}")
        s, _ = req("PATCH", f"/api/tasks/{t2['task_id']}?updater_id={a1}",
                   {"status": "done"}, key=API_KEY)
        check("完成依赖 T2", s == 200, f"status={s}")
        s, t3now = req("GET", f"/api/tasks/{t3['task_id']}")
        check("下游任务被解锁分配", t3now.get("status") != "pending" or t3now.get("assignee_id"),
              f"status={t3now.get('status')} assignee={t3now.get('assignee_id')}")

        print("\n[10] result 可显式置空（M3）")
        s, tx = req("POST", "/api/tasks", {"title": "TX", "creator_id": a1}, key=API_KEY)
        s, _ = req("PATCH", f"/api/tasks/{tx['task_id']}?updater_id={a1}",
                   {"result": {"ok": 1}}, key=API_KEY)
        s, _ = req("PATCH", f"/api/tasks/{tx['task_id']}?updater_id={a1}",
                   {"result": None}, key=API_KEY)
        s, txnow = req("GET", f"/api/tasks/{tx['task_id']}")
        check("result 置空生效", txnow.get("result") is None,
              f"result={txnow.get('result')!r}")

        print("\n[11] 并发负载计数（S2）")
        s, ca = req("POST", "/api/agents", {"name": "LoadAgent", "capabilities": ["python"]},
                    key=API_KEY)
        cid_a = ca["agent_id"]
        import concurrent.futures as cf
        tasks = []
        for i in range(8):
            s, tt = req("POST", "/api/tasks", {"title": f"L{i}", "creator_id": a1}, key=API_KEY)
            tasks.append(tt["task_id"])

        def assign(tid):
            return req("POST", f"/api/tasks/{tid}/assign?assignee_id={cid_a}", None, key=API_KEY)[0]

        with cf.ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(assign, tasks))
        s, ag = req("GET", f"/api/agents/{cid_a}")
        check("并发分配负载计数正确", ag.get("current_task_count") == 8,
              f"count={ag.get('current_task_count')} 期望 8")

        print("\n[12] 非法 datetime 序列化（M5）")
        s, _ = req("POST", f"/api/entries?author_id={a1}",
                   {"topic": "json-test", "content": {"ts": "2026-09-30T12:00:00Z", "n": 1}},
                   key=API_KEY)
        check("嵌套 JSON 内容可写入", s == 201, f"status={s}")

        print("\n[13] 依赖校验")
        s, c1 = req("POST", "/api/tasks", {"title": "C1", "creator_id": a1}, key=API_KEY)
        s, c2 = req("POST", "/api/tasks",
                    {"title": "C2", "creator_id": a1, "dependencies": [c1["task_id"]]},
                    key=API_KEY)
        check("链式依赖创建成功", s == 201, f"status={s}")
        s, resp = req("POST", "/api/tasks",
                      {"title": "C3", "creator_id": a1,
                       "dependencies": ["00000000-0000-0000-0000-000000000000"]}, key=API_KEY)
        check("不存在的依赖被拒（404）", s == 404, f"status={s}")

    finally:
        proc.terminate()
        try:
            out = proc.communicate(timeout=10)[0]
            if out:
                text = out.decode(errors="replace")
                errs = [l for l in text.splitlines()
                        if "Traceback" in l or "ERROR" in l or "Exception" in l]
                if errs:
                    print("\n服务端错误日志：")
                    for l in errs[:15]:
                        print("   ", l)
        except Exception:
            proc.kill()

    print("\n" + "=" * 56)
    print(f"结果：{len(passed)} PASS / {len(failed)} FAIL")
    if failed:
        print("失败项：")
        for f in failed:
            print("   -", f)
    print("=" * 56)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
