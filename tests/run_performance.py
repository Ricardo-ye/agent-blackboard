"""在自启的临时服务上运行性能测试（无需手工起服务器）。"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8124
BASE = f"http://127.0.0.1:{PORT}"
API_KEY = "perf-secret-key"


def wait_ready(timeout: float = 40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/", timeout=3) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="启动隔离服务并运行性能测试")
    parser.add_argument(
        "--report",
        metavar="PATH",
        help="可选：将结构化 JSON 报告写入指定路径；默认只打印结果。",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tmpdir = tempfile.mkdtemp(prefix="perf-")
    env = dict(os.environ)
    env["BLACKBOARD_DB_PATH"] = str(Path(tmpdir) / "perf.db")
    env["BLACKBOARD_API_KEY"] = API_KEY
    env["PYTHONPATH"] = str(ROOT)
    env["PYTHONUNBUFFERED"] = "1"
    env["BLACKBOARD_BASE_URL"] = BASE
    if args.report:
        env["BLACKBOARD_PERFORMANCE_REPORT"] = str(
            Path(args.report).expanduser().resolve()
        )

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--port",
            str(PORT),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        if not wait_ready():
            print("服务未就绪")
            process.terminate()
            print(process.communicate(timeout=10)[0].decode(errors="replace")[:2000])
            return 1
        print(f"性能测试服务就绪：{BASE}\n")
        result = subprocess.run(
            [sys.executable, str(ROOT / "tests" / "performance_test.py")],
            cwd=ROOT,
            env=env,
            timeout=600,
        )
        return result.returncode
    finally:
        process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


if __name__ == "__main__":
    sys.exit(main())
