"""在自启的临时服务上运行性能测试（无需手工起服务器）"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8124
BASE = f"http://127.0.0.1:{PORT}"
API_KEY = "perf-secret-key"


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


def main():
    tmpdir = tempfile.mkdtemp(prefix="perf-")
    env = dict(os.environ)
    env["BLACKBOARD_DB_PATH"] = os.path.join(tmpdir, "perf.db")
    env["BLACKBOARD_API_KEY"] = API_KEY
    env["PYTHONPATH"] = ROOT
    env["PYTHONUNBUFFERED"] = "1"
    env["BLACKBOARD_BASE_URL"] = BASE

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", str(PORT),
         "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    try:
        if not wait_ready():
            print("服务未就绪")
            proc.terminate()
            print(proc.communicate(timeout=10)[0].decode(errors="replace")[:2000])
            return 1
        print(f"性能测试服务就绪：{BASE}\n")
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, "tests", "performance_test.py")],
            cwd=ROOT, env=env, timeout=600,
        )
        return r.returncode
    finally:
        proc.terminate()
        try:
            proc.communicate(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
