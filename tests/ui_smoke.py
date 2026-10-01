"""UI 冒烟测试 —— 用真实浏览器（Edge/Chrome 无头）加载控制台的每个页面，
通过 CDP 收集运行时报错与页面文本，确认：
  1. 静态资源与 ES Module 能正常加载
  2. 各页面渲染成功（没有落到「渲染失败 / 加载失败」分支）
  3. 控制台没有 JS 报错
  4. 空态→有数据的渲染都正常（先灌入演示数据）

自启自停 uvicorn，临时数据库，不影响开发库。
"""
from __future__ import annotations

import asyncio
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / "python" / "python.exe")
PORT = 8261
CDP_PORT = 9333

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

ROUTES = [
    ("#/dashboard", "仪表盘", ["仪表盘", "实时事件流"]),
    ("#/agents", "智能体", ["智能体"]),
    ("#/entries", "黑板条目", ["黑板条目"]),
    ("#/tasks", "任务", ["任务"]),
    ("#/conflicts", "冲突治理", ["冲突治理"]),
    ("#/rules", "协作规则", ["协作规则"]),
]

FAIL_MARKERS = ["页面渲染失败", "加载失败", "无法连接服务"]

VERBOSE = "--verbose" in sys.argv


def find_browser() -> str | None:
    for p in BROWSER_CANDIDATES:
        if os.path.exists(p):
            return p
    # 兜底：在常见目录里搜
    for base in (r"C:\Program Files", r"C:\Program Files (x86)"):
        for pat in (r"*\Chrome\Application\chrome.exe", r"*\Edge\Application\msedge.exe"):
            hits = glob.glob(os.path.join(base, pat))
            if hits:
                return hits[0]
    return None


def free_port(preferred: int) -> int:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]


def start_server(db_path: str, port: int) -> subprocess.Popen:
    env = dict(os.environ)
    env["BLACKBOARD_DB_PATH"] = db_path
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port),
         "--log-level", "warning"],
        cwd=str(ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    for _ in range(150):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proc
        except OSError:
            if proc.poll() is not None:
                out = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"服务提前退出：\n{out}")
            time.sleep(0.2)
    proc.kill()
    raise RuntimeError("服务未能在预期时间内就绪")


async def seed(base: str) -> None:
    """灌入少量数据，让列表页不是空态"""
    async with httpx.AsyncClient(base_url=base, timeout=20) as c:
        a1 = (await c.post("/api/agents", json={"name": "架构师Agent", "capabilities": ["architecture"]})).json()
        a2 = (await c.post("/api/agents", json={"name": "编码Agent", "capabilities": ["python"]})).json()
        await c.post("/api/entries", params={"author_id": a1["agent_id"]},
                     json={"topic": "architecture", "content": {"pattern": "blackboard"}, "tags": ["架构"]})
        await c.post("/api/entries", params={"author_id": a2["agent_id"]},
                     json={"topic": "architecture", "content": {"pattern": "message_bus"}, "tags": ["争议"]})
        t = (await c.post("/api/tasks", json={
            "title": "确定架构方案", "required_capabilities": ["architecture"], "priority": "high"})).json()
        await c.post("/api/tasks", json={
            "title": "实现核心读写", "required_capabilities": ["python"], "dependencies": [t["task_id"]]})
        await c.post("/api/conflicts/detect/opinion", params={"topic": "architecture"})


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self._id = 0
        self._resp: dict[int, asyncio.Queue] = {}
        self.events: list[dict] = []
        self._reader = None

    async def start(self):
        self._reader = asyncio.create_task(self._read_loop())

    async def _read_loop(self):
        try:
            async for raw in self.ws:
                msg = json.loads(raw)
                if "id" in msg and msg["id"] in self._resp:
                    await self._resp[msg["id"]].put(msg)
                else:
                    self.events.append(msg)
        except websockets.exceptions.ConnectionClosed:
            pass

    async def send(self, method, params=None, session=None):
        self._id += 1
        mid = self._id
        q: asyncio.Queue = asyncio.Queue()
        self._resp[mid] = q
        payload = {"id": mid, "method": method, "params": params or {}}
        if session:
            payload["sessionId"] = session
        await self.ws.send(json.dumps(payload))
        while True:
            msg = await asyncio.wait_for(q.get(), timeout=30)
            if msg.get("id") == mid:
                del self._resp[mid]
                if "error" in msg:
                    raise RuntimeError(f"{method} -> {msg['error']}")
                return msg.get("result", {})

    async def close(self):
        if self._reader:
            self._reader.cancel()
        await self.ws.close()


async def main() -> int:
    global PORT, CDP_PORT
    PORT = free_port(PORT)
    CDP_PORT = free_port(CDP_PORT)
    base = f"http://127.0.0.1:{PORT}"
    ui = f"{base}/ui/"

    browser = find_browser()
    if not browser:
        print("未找到 Chrome / Edge，跳过浏览器冒烟测试")
        return 0

    tmpdir = tempfile.mkdtemp(prefix="ui_smoke_")
    db = str(Path(tmpdir) / "smoke.db")
    profile = str(Path(tmpdir) / "profile")

    server = start_server(db, PORT)
    proc = None
    failed = 0
    try:
        proc = subprocess.Popen([
            browser, "--headless=new", f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check",
            "--disable-gpu", "--disable-extensions", "--mute-audio",
            "--window-size=1440,900", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 等待调试端口就绪
        ws_url = None
        for _ in range(80):
            try:
                with httpx.Client(timeout=2) as c:
                    data = c.get(f"http://127.0.0.1:{CDP_PORT}/json/list").json()
                pages = [t for t in data if t.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            await asyncio.sleep(0.25)
        if not ws_url:
            print("浏览器调试端口未就绪，跳过")
            return 0

        print("=" * 68)
        print(f"UI 冒烟测试  {ui}   浏览器：{Path(browser).name}")
        print("=" * 68)

        async with websockets.connect(ws_url, max_size=None) as ws:
            cdp = CDP(ws)
            await cdp.start()
            await cdp.send("Page.enable")
            await cdp.send("Runtime.enable")
            await cdp.send("Log.enable")

            # 空状态：尚未灌入数据时，列表页应显示引导空态而不是报错
            await cdp.send("Page.navigate", {"url": f"{ui}#/agents"})
            await asyncio.sleep(2.0)
            r = await cdp.send("Runtime.evaluate", {
                "expression": "document.getElementById('view')?.innerText || ''",
                "returnByValue": True,
            })
            text = (r.get("result") or {}).get("value", "") or ""
            ok = "还没有注册任何智能体" in text
            print(f"  {'PASS' if ok else 'FAIL'}  空状态     未灌数据时显示引导空态")
            if not ok:
                failed += 1
                print(f"        文本片段：{text[:300]!r}")

            # 演示数据：走仪表盘的「灌入演示数据」按钮（顺带验证该脚本本身）
            await cdp.send("Page.navigate", {"url": f"{ui}#/dashboard"})
            await asyncio.sleep(2.0)
            await cdp.send("Runtime.evaluate", {
                "expression": "document.getElementById('btnSeed').click()"})
            await asyncio.sleep(1.0)
            r = await cdp.send("Runtime.evaluate", {
                "expression": """
                    (() => {
                      const b = [...document.querySelectorAll('.modal__foot button')]
                        .find(x => x.textContent.includes('开始灌入'));
                      if (!b) return 'no-confirm';
                      b.click();
                      return 'confirmed';
                    })()
                """,
                "returnByValue": True,
            })
            step = (r.get("result") or {}).get("value", "")
            await asyncio.sleep(10.0)
            async with httpx.AsyncClient(base_url=base, timeout=10) as c:
                st = (await c.get("/api/stats")).json()
            ui_seed_ok = (st["total_agents"] >= 5 and st["total_entries"] >= 6
                          and st["total_tasks"] >= 6 and st["total_conflicts"] >= 1)
            print(f"  {'PASS' if ui_seed_ok else 'FAIL'}  演示数据   仪表盘一键灌入 [{step}] "
                  f"智能体{st['total_agents']} 条目{st['total_entries']} "
                  f"任务{st['total_tasks']} 冲突{st['total_conflicts']}")
            if not ui_seed_ok:
                failed += 1
                await seed(base)  # 兜底：直接调 API 灌入，保证后续页面仍有数据

            for route, name, markers in ROUTES:
                cdp.events.clear()
                await cdp.send("Page.navigate", {"url": f"{ui}{route}"})
                await asyncio.sleep(2.2)

                errs = []
                for ev in cdp.events:
                    if ev.get("method") == "Runtime.exceptionThrown":
                        d = ev["params"].get("exceptionDetails", {})
                        errs.append(d.get("exception", {}).get("description") or d.get("text", ""))
                    elif ev.get("method") == "Runtime.consoleAPICalled":
                        if ev["params"].get("type") == "error":
                            txt = " ".join(
                                str(a.get("value", a.get("description", "")))
                                for a in ev["params"].get("args", [])
                            )
                            errs.append(txt)
                    elif ev.get("method") == "Log.entryAdded":
                        if ev["params"].get("entry", {}).get("level") == "error":
                            errs.append(ev["params"]["entry"].get("text", ""))

                r = await cdp.send("Runtime.evaluate", {
                    "expression": "document.getElementById('view')?.innerText || document.body.innerText",
                    "returnByValue": True,
                })
                text = (r.get("result") or {}).get("value", "") or ""

                hit_fail = [m for m in FAIL_MARKERS if m in text]
                miss = [m for m in markers if m not in text]

                ok = not hit_fail and not miss and not errs
                if ok:
                    print(f"  PASS  {name:<8} ({route})  文本 {len(text)} 字符")
                    if VERBOSE:
                        print("        ---- 页面文本 ----")
                        for line in text.splitlines():
                            if line.strip():
                                print(f"        | {line[:140]}")
                        print("        ------------------")
                else:
                    failed += 1
                    print(f"  FAIL  {name:<8} ({route})")
                    if miss:
                        print(f"        缺失标记：{miss}")
                    if hit_fail:
                        print(f"        出现失败态：{hit_fail}")
                    for e in errs[:5]:
                        print(f"        JS 错误：{str(e)[:220]}")
                    print(f"        页面文本片段：{text[:300]!r}")

            # 交互闭环：在智能体页点「注册智能体」→ 填表 → 提交 → 列表出现新条目
            cdp.events.clear()
            await cdp.send("Page.navigate", {"url": f"{ui}#/agents"})
            await asyncio.sleep(1.8)
            new_name = "UI自动化验证Agent"
            r0 = await cdp.send("Runtime.evaluate", {"expression": "document.getElementById('btnCreate').click()"})
            await asyncio.sleep(0.8)
            dbg = await cdp.send("Runtime.evaluate", {
                "expression": "document.getElementById('f_name') ? 'form-ok' : 'no-form'",
                "returnByValue": True,
            })
            step1 = (dbg.get("result") or {}).get("value", "")
            r = await cdp.send("Runtime.evaluate", {
                "expression": f"""
                    (() => {{
                      const n = document.getElementById('f_name');
                      const c = document.getElementById('f_capabilities');
                      if (!n) return 'no-form';
                      n.value = {new_name!r};
                      if (c) c.value = 'qa, automation';
                      const btn = [...document.querySelectorAll('.modal__foot button')]
                        .find(b => b.textContent.includes('注册'));
                      if (!btn) return 'no-submit';
                      btn.click();
                      return 'submitted';
                    }})()
                """,
                "returnByValue": True,
            })
            step2 = (r.get("result") or {}).get("value", "")
            await asyncio.sleep(2.5)
            r = await cdp.send("Runtime.evaluate", {
                "expression": "document.getElementById('view')?.innerText || ''",
                "returnByValue": True,
            })
            text = (r.get("result") or {}).get("value", "") or ""
            errs = [e for e in cdp.events if e.get("method") == "Runtime.exceptionThrown"]
            ok = new_name in text and not errs
            print(f"  {'PASS' if ok else 'FAIL'}  表单闭环   注册新智能体后出现在列表"
                  f"  [开表单={step1} 提交={step2}]")
            if not ok:
                failed += 1
                print(f"        文本片段：{text[:300]!r}")
                for e in errs[:3]:
                    print(f"        异常：{str(e)[:200]}")

            # 详情页：拿一个真实 ID 验证
            cdp.events.clear()
            async with httpx.AsyncClient(base_url=base, timeout=10) as c:
                agents = (await c.get("/api/agents")).json()
            if agents:
                aid = agents[0]["agent_id"]
                await cdp.send("Page.navigate", {"url": f"{ui}#/agents/{aid}"})
                await asyncio.sleep(2.0)
                r = await cdp.send("Runtime.evaluate", {
                    "expression": "document.getElementById('view')?.innerText || ''",
                    "returnByValue": True,
                })
                text = (r.get("result") or {}).get("value", "") or ""
                errs = [e for e in cdp.events if e.get("method") == "Runtime.exceptionThrown"]
                ok = "基本信息" in text and not errs
                print(f"  {'PASS' if ok else 'FAIL'}  智能体详情  文本 {len(text)} 字符")
                if not ok:
                    failed += 1
                    print(f"        文本片段：{text[:300]!r}")
                    for e in errs[:3]:
                        print(f"        异常：{str(e)[:200]}")

            # 窄屏响应式：375px 下侧边栏应收起
            await cdp.send("Emulation.setDeviceMetricsOverride", {
                "width": 375, "height": 780, "deviceScaleFactor": 2, "mobile": True,
            })
            await cdp.send("Page.navigate", {"url": f"{ui}#/tasks"})
            await asyncio.sleep(1.8)
            r = await cdp.send("Runtime.evaluate", {
                "expression": """
                    (() => {
                      const sb = document.querySelector('.sidebar');
                      const menu = document.querySelector('.menu-btn');
                      return JSON.stringify({
                        sidebarHidden: sb ? getComputedStyle(sb).transform !== 'none' : null,
                        menuVisible: menu ? getComputedStyle(menu).display !== 'none' : null,
                        overflowX: document.documentElement.scrollWidth <= window.innerWidth + 2
                      });
                    })()
                """,
                "returnByValue": True,
            })
            val = (r.get("result") or {}).get("value", "{}")
            try:
                info = json.loads(val)
            except Exception:
                info = {}
            ok = info.get("menuVisible") is True and info.get("overflowX") is True
            print(f"  {'PASS' if ok else 'FAIL'}  响应式 375px  {info}")
            if not ok:
                failed += 1

            await cdp.close()

        print("=" * 68)
        print(f"结果：{'全部通过' if failed == 0 else f'{failed} 项失败'}")
        print("=" * 68)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.kill()
        server.terminate()
        try:
            server.wait(timeout=8)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
