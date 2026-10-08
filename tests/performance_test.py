"""性能测试：测量延迟、并发、吞吐和资源占用，并可选输出结构化报告。"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import statistics
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

BASE_URL = os.getenv("BLACKBOARD_BASE_URL", "http://127.0.0.1:8000")
WS_URL = (
    ("wss://" if BASE_URL.startswith("https://") else "ws://")
    + BASE_URL.split("://", 1)[-1].rstrip("/")
    + "/ws"
)
API_KEY = os.getenv("BLACKBOARD_API_KEY", "")
REPORT_ENV = "BLACKBOARD_PERFORMANCE_REPORT"


def create_client() -> httpx.AsyncClient:
    """创建带大连接池的 HTTP 客户端。"""
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    return httpx.AsyncClient(limits=limits, timeout=30.0, headers=headers)


def summarize_samples(samples: list[float]) -> dict[str, float | int]:
    """以毫秒汇总样本，空样本直接失败而不产生误导性报告。"""
    if not samples:
        raise ValueError("无法汇总空性能样本")
    ordered = sorted(samples)
    return {
        "count": len(samples),
        "mean_ms": statistics.mean(samples),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "p95_ms": ordered[int(len(ordered) * 0.95)],
    }


def write_report(path: str | Path, payload: dict[str, Any]) -> Path:
    """原子写入 JSON 报告，避免中断时留下半份可被误读的基线。"""
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return target


async def measure_response_time(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    **kwargs: Any,
) -> float:
    """测量单次成功 HTTP 请求的响应时间（ms）。"""
    started_at = time.perf_counter()
    response = await client.request(method, f"{BASE_URL}{path}", **kwargs)
    await response.aread()
    response.raise_for_status()
    return (time.perf_counter() - started_at) * 1000


async def test_single_request_performance() -> dict[str, dict[str, float | int]]:
    """测试单请求响应时间。"""
    print("\n" + "=" * 60)
    print("1. 单请求响应时间测试")
    print("=" * 60)

    report: dict[str, dict[str, float | int]] = {}
    async with create_client() as client:
        agents = [
            await measure_response_time(
                client,
                "POST",
                "/api/agents",
                json={"name": f"PerfAgent_{index}", "capabilities": ["python", "test"]},
            )
            for index in range(10)
        ]
        report["agent_registration"] = summarize_samples(agents)

        agent_listing = [
            await measure_response_time(client, "GET", "/api/agents")
            for _ in range(10)
        ]
        report["agent_listing"] = summarize_samples(agent_listing)

        agents_response = await client.get(f"{BASE_URL}/api/agents")
        agents_response.raise_for_status()
        creator_id = agents_response.json()[0]["agent_id"]

        task_creation = [
            await measure_response_time(
                client,
                "POST",
                "/api/tasks",
                json={
                    "title": f"PerfTask_{index}",
                    "required_capabilities": ["python"],
                    "creator_id": creator_id,
                    "priority": "normal",
                },
            )
            for index in range(10)
        ]
        report["task_creation"] = summarize_samples(task_creation)

        entry_creation = [
            await measure_response_time(
                client,
                "POST",
                f"/api/entries?author_id={creator_id}",
                json={"topic": "perf_test", "content": {"index": index}, "tags": ["perf"]},
            )
            for index in range(10)
        ]
        report["entry_creation"] = summarize_samples(entry_creation)

    for name, metrics in report.items():
        print(f"  {name} ({int(metrics['count'])}次):")
        print(f"    平均: {metrics['mean_ms']:.2f}ms")
        print(f"    最小: {metrics['min_ms']:.2f}ms")
        print(f"    最大: {metrics['max_ms']:.2f}ms")
        print(f"    P95:  {metrics['p95_ms']:.2f}ms")
    return report


async def test_concurrent_performance() -> list[dict[str, float | int]]:
    """测试不同并发级别下的任务创建能力。"""
    print("\n" + "=" * 60)
    print("2. 并发处理能力测试")
    print("=" * 60)

    results: list[dict[str, float | int]] = []
    async with create_client() as client:
        for index in range(20):
            response = await client.post(
                f"{BASE_URL}/api/agents",
                json={"name": f"ConcurrentAgent_{index}", "capabilities": ["python"]},
            )
            response.raise_for_status()

        agents_response = await client.get(f"{BASE_URL}/api/agents")
        agents_response.raise_for_status()
        creator_id = agents_response.json()[0]["agent_id"]

        for concurrency in (10, 25, 50, 100):
            started_at = time.perf_counter()

            async def create_task(index: int) -> int:
                response = await client.post(
                    f"{BASE_URL}/api/tasks",
                    json={
                        "title": f"ConcurrentTask_{concurrency}_{index}",
                        "required_capabilities": ["python"],
                        "creator_id": creator_id,
                    },
                )
                return response.status_code

            status_codes = await asyncio.gather(
                *(create_task(index) for index in range(concurrency))
            )
            elapsed_seconds = time.perf_counter() - started_at
            success_count = sum(status == 201 for status in status_codes)
            result = {
                "concurrency": concurrency,
                "elapsed_ms": elapsed_seconds * 1000,
                "success_count": success_count,
                "request_count": concurrency,
                "throughput_requests_per_second": concurrency / elapsed_seconds,
                "mean_latency_ms": elapsed_seconds / concurrency * 1000,
            }
            results.append(result)
            if success_count != concurrency:
                raise RuntimeError(
                    "Concurrent task creation returned unexpected status codes: "
                    f"{status_codes}"
                )
            print(f"  并发数={concurrency}:")
            print(f"    总耗时: {result['elapsed_ms']:.2f}ms")
            print(f"    成功数: {success_count}/{concurrency}")
            print(f"    吞吐量: {result['throughput_requests_per_second']:.1f} req/s")
            print(f"    平均延迟: {result['mean_latency_ms']:.2f}ms")
    return results


async def test_data_throughput() -> dict[str, Any]:
    """测量单条写入、批量写入和读取吞吐。"""
    print("\n" + "=" * 60)
    print("3. 数据吞吐量测试")
    print("=" * 60)

    report: dict[str, Any] = {"concurrent_entry_writes": [], "entry_reads": []}
    async with create_client() as client:
        agents_response = await client.get(f"{BASE_URL}/api/agents")
        agents_response.raise_for_status()
        author_id = agents_response.json()[0]["agent_id"]
        semaphore = asyncio.Semaphore(20)

        for batch_size in (50, 100, 200):
            started_at = time.perf_counter()

            async def create_entry(index: int) -> int:
                async with semaphore:
                    response = await client.post(
                        f"{BASE_URL}/api/entries?author_id={author_id}",
                        json={
                            "topic": "throughput_test",
                            "content": {"data": list(range(50)), "meta": f"entry_{index}"},
                            "tags": ["bench"],
                        },
                    )
                    return response.status_code

            status_codes = await asyncio.gather(
                *(create_entry(index) for index in range(batch_size))
            )
            elapsed_seconds = time.perf_counter() - started_at
            success_count = sum(status == 201 for status in status_codes)
            result = {
                "entry_count": batch_size,
                "concurrency_limit": 20,
                "success_count": success_count,
                "elapsed_ms": elapsed_seconds * 1000,
                "throughput_entries_per_second": success_count / elapsed_seconds,
            }
            report["concurrent_entry_writes"].append(result)
            if success_count != batch_size:
                raise RuntimeError(
                    "Concurrent entry writes returned unexpected status codes: "
                    f"{status_codes}"
                )
            print(f"  单条 API 创建 {batch_size} 条条目 (并发限制=20):")
            print(f"    总耗时: {result['elapsed_ms']:.2f}ms")
            print(f"    成功数: {success_count}/{batch_size}")
            print(f"    吞吐量: {result['throughput_entries_per_second']:.1f} entries/s")

        for request_count in (100, 500):
            started_at = time.perf_counter()
            for _ in range(request_count):
                response = await client.get(f"{BASE_URL}/api/entries?topic=throughput_test")
                response.raise_for_status()
            elapsed_seconds = time.perf_counter() - started_at
            result = {
                "request_count": request_count,
                "elapsed_ms": elapsed_seconds * 1000,
                "throughput_queries_per_second": request_count / elapsed_seconds,
            }
            report["entry_reads"].append(result)
            print(f"  批量查询 {request_count} 次:")
            print(f"    总耗时: {result['elapsed_ms']:.2f}ms")
            print(f"    吞吐量: {result['throughput_queries_per_second']:.1f} queries/s")

        batch_size = 200
        payload = {
            "entries": [
                {
                    "topic": "batch-api-performance",
                    "content": {"index": index, "data": "x" * 100},
                }
                for index in range(batch_size)
            ]
        }
        started_at = time.perf_counter()
        response = await client.post(
            f"{BASE_URL}/api/entries/batch",
            params={"author_id": author_id},
            json=payload,
        )
        elapsed_seconds = time.perf_counter() - started_at
        response.raise_for_status()
        created = response.json()
        if len(created) != batch_size:
            raise RuntimeError(f"批量 API 返回数量错误: {len(created)}/{batch_size}")
        report["batch_api_write"] = {
            "entry_count": batch_size,
            "success_count": len(created),
            "elapsed_ms": elapsed_seconds * 1000,
            "throughput_entries_per_second": batch_size / elapsed_seconds,
        }
        print(f"  批量 API 创建 {batch_size} 条:")
        print(f"    总耗时: {report['batch_api_write']['elapsed_ms']:.2f}ms")
        print(
            "    吞吐量: "
            f"{report['batch_api_write']['throughput_entries_per_second']:.1f} entries/s"
        )
    return report


async def test_resource_usage() -> dict[str, Any]:
    """测量测试进程的 Python 内存分配和最终业务实体数量。"""
    print("\n" + "=" * 60)
    print("4. 资源占用测试")
    print("=" * 60)

    tracemalloc.start()
    try:
        async with create_client() as client:
            agents_response = await client.get(f"{BASE_URL}/api/agents")
            agents_response.raise_for_status()
            author_id = agents_response.json()[0]["agent_id"]

            for index in range(50):
                responses = (
                    await client.post(
                        f"{BASE_URL}/api/agents",
                        json={"name": f"ResourceAgent_{index}", "capabilities": ["test"]},
                    ),
                    await client.post(
                        f"{BASE_URL}/api/tasks",
                        json={"title": f"ResourceTask_{index}", "required_capabilities": ["test"]},
                    ),
                    await client.post(
                        f"{BASE_URL}/api/entries?author_id={author_id}",
                        json={"topic": "resource_test", "content": {"index": index}},
                    ),
                    await client.get(f"{BASE_URL}/api/stats"),
                )
                for response in responses:
                    response.raise_for_status()

            current_bytes, peak_bytes = tracemalloc.get_traced_memory()
            stats_response = await client.get(f"{BASE_URL}/api/stats")
            stats_response.raise_for_status()
            stats = stats_response.json()
    finally:
        tracemalloc.stop()

    result = {
        "tracemalloc_current_mb": current_bytes / 1024 / 1024,
        "tracemalloc_peak_mb": peak_bytes / 1024 / 1024,
        "system_stats": stats,
    }
    print("  内存占用 (tracemalloc):")
    print(f"    当前: {result['tracemalloc_current_mb']:.2f} MB")
    print(f"    峰值: {result['tracemalloc_peak_mb']:.2f} MB")
    print("\n  系统最终状态:")
    for label, field in (("智能体总数", "total_agents"), ("任务总数", "total_tasks"),
                         ("条目总数", "total_entries"), ("规则总数", "total_rules")):
        print(f"    {label}: {stats[field]}")
    return result


async def test_websocket_performance() -> dict[str, float]:
    """测量 HTTP 创建任务到 WebSocket 订阅者收到事件的端到端延迟。"""
    print("\n" + "=" * 60)
    print("5. WebSocket实时推送性能测试")
    print("=" * 60)

    try:
        import websockets
    except ImportError as error:
        raise RuntimeError("websockets 库未安装，无法完成性能测试") from error

    async with websockets.connect(WS_URL) as websocket:
        await websocket.send(json.dumps({"action": "subscribe", "channels": ["task.created", "tasks"]}))
        await websocket.recv()

        async with create_client() as client:
            agents_response = await client.get(f"{BASE_URL}/api/agents")
            agents_response.raise_for_status()
            creator_id = agents_response.json()[0]["agent_id"]

            latencies = []
            for index in range(50):
                started_at = time.perf_counter()
                response = await client.post(
                    f"{BASE_URL}/api/tasks",
                    json={
                        "title": f"WS_Perf_{index}",
                        "required_capabilities": ["python"],
                        "creator_id": creator_id,
                    },
                )
                response.raise_for_status()
                try:
                    await asyncio.wait_for(websocket.recv(), timeout=2.0)
                except asyncio.TimeoutError as error:
                    raise RuntimeError("未在 2 秒内收到 WebSocket 推送") from error
                latencies.append((time.perf_counter() - started_at) * 1000)

    result = summarize_samples(latencies)
    print(f"  WebSocket推送延迟 ({int(result['count'])}次):")
    print(f"    平均: {result['mean_ms']:.2f}ms")
    print(f"    最小: {result['min_ms']:.2f}ms")
    print(f"    最大: {result['max_ms']:.2f}ms")
    print(f"    P95:  {result['p95_ms']:.2f}ms")
    return result


async def main() -> None:
    generated_at = datetime.now(timezone.utc)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": generated_at.isoformat(),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "base_url": BASE_URL,
        },
        "benchmarks": {},
    }
    print("=" * 60)
    print("智能体动态协作黑板系统 - 性能测试")
    print(f"测试时间: {generated_at.isoformat()}")
    print("=" * 60)

    report["benchmarks"]["single_request_latency"] = await test_single_request_performance()
    report["benchmarks"]["concurrent_task_creation"] = await test_concurrent_performance()
    report["benchmarks"]["data_throughput"] = await test_data_throughput()
    report["benchmarks"]["resource_usage"] = await test_resource_usage()
    report["benchmarks"]["websocket_latency"] = await test_websocket_performance()

    report_path = os.getenv(REPORT_ENV)
    if report_path:
        target = write_report(report_path, report)
        print(f"\n结构化报告已写入: {target}")

    print("\n" + "=" * 60)
    print("性能测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
