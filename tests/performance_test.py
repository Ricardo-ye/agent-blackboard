"""性能测试脚本 - 测量系统响应时间、并发能力、吞吐量与资源占用"""
import sys
import os
import asyncio
import time
import json
import statistics
import tracemalloc
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

BASE_URL = os.getenv("BLACKBOARD_BASE_URL", "http://127.0.0.1:8000")
WS_URL = (
    ("wss://" if BASE_URL.startswith("https://") else "ws://")
    + BASE_URL.split("://", 1)[-1].rstrip("/")
    + "/ws"
)

# 与服务端 BLACKBOARD_API_KEY 保持一致；未配置时为空（服务端也不校验）
API_KEY = os.getenv("BLACKBOARD_API_KEY", "")


def create_client() -> httpx.AsyncClient:
    """创建带大连接池的HTTP客户端"""
    limits = httpx.Limits(max_connections=200, max_keepalive_connections=100)
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    return httpx.AsyncClient(limits=limits, timeout=30.0, headers=headers)


async def measure_response_time(client: httpx.AsyncClient, method: str, path: str, **kwargs) -> float:
    """测量单次请求响应时间(ms)"""
    start = time.perf_counter()
    response = await client.request(method, f"{BASE_URL}{path}", **kwargs)
    await response.aread()
    elapsed = (time.perf_counter() - start) * 1000
    return elapsed


async def test_single_request_performance():
    """测试单请求响应时间"""
    print("\n" + "=" * 60)
    print("1. 单请求响应时间测试")
    print("=" * 60)

    async with create_client() as client:
        # 注册智能体
        times = []
        for i in range(10):
            t = await measure_response_time(
                client, "POST", "/api/agents",
                json={"name": f"PerfAgent_{i}", "capabilities": ["python", "test"]}
            )
            times.append(t)

        print(f"  智能体注册 (10次):")
        print(f"    平均: {statistics.mean(times):.2f}ms")
        print(f"    最小: {min(times):.2f}ms")
        print(f"    最大: {max(times):.2f}ms")
        print(f"    P95:  {sorted(times)[int(len(times)*0.95)]:.2f}ms")

        # 获取智能体列表
        times = []
        for i in range(10):
            t = await measure_response_time(client, "GET", "/api/agents")
            times.append(t)
        print(f"  智能体列表查询 (10次):")
        print(f"    平均: {statistics.mean(times):.2f}ms")
        print(f"    P95:  {sorted(times)[int(len(times)*0.95)]:.2f}ms")

        # 创建任务
        agents_response = await client.get(f"{BASE_URL}/api/agents")
        agents = agents_response.json()
        creator_id = agents[0]["agent_id"] if agents else None

        times = []
        for i in range(10):
            t = await measure_response_time(
                client, "POST", "/api/tasks",
                json={"title": f"PerfTask_{i}", "required_capabilities": ["python"],
                      "creator_id": creator_id, "priority": "normal"}
            )
            times.append(t)
        print(f"  任务创建 (10次):")
        print(f"    平均: {statistics.mean(times):.2f}ms")
        print(f"    P95:  {sorted(times)[int(len(times)*0.95)]:.2f}ms")

        # 创建黑板条目
        times = []
        for i in range(10):
            t = await measure_response_time(
                client, "POST", f"/api/entries?author_id={creator_id}",
                json={"topic": "perf_test", "content": {"index": i}, "tags": ["perf"]}
            )
            times.append(t)
        print(f"  黑板条目创建 (10次):")
        print(f"    平均: {statistics.mean(times):.2f}ms")
        print(f"    P95:  {sorted(times)[int(len(times)*0.95)]:.2f}ms")


async def test_concurrent_performance():
    """测试并发处理能力"""
    print("\n" + "=" * 60)
    print("2. 并发处理能力测试")
    print("=" * 60)

    concurrency_levels = [10, 25, 50, 100]

    async with create_client() as client:
        # 先注册足够的智能体
        for i in range(20):
            await client.post(f"{BASE_URL}/api/agents",
                              json={"name": f"ConcurrentAgent_{i}", "capabilities": ["python"]})

        agents_response = await client.get(f"{BASE_URL}/api/agents")
        agents = agents_response.json()
        creator_id = agents[0]["agent_id"]

        for concurrency in concurrency_levels:
            # 并发创建任务
            start = time.perf_counter()

            async def create_task(idx):
                resp = await client.post(
                    f"{BASE_URL}/api/tasks",
                    json={"title": f"ConcurrentTask_{concurrency}_{idx}",
                          "required_capabilities": ["python"],
                          "creator_id": creator_id}
                )
                return resp.status_code

            tasks = [create_task(i) for i in range(concurrency)]
            results = await asyncio.gather(*tasks)
            elapsed = time.perf_counter() - start

            success_count = sum(1 for r in results if r == 201)
            throughput = concurrency / elapsed

            print(f"  并发数={concurrency}:")
            print(f"    总耗时: {elapsed*1000:.2f}ms")
            print(f"    成功数: {success_count}/{concurrency}")
            print(f"    吞吐量: {throughput:.1f} req/s")
            print(f"    平均延迟: {(elapsed/concurrency)*1000:.2f}ms")


async def test_data_throughput():
    """测试数据吞吐量"""
    print("\n" + "=" * 60)
    print("3. 数据吞吐量测试")
    print("=" * 60)

    async with create_client() as client:
        agents_response = await client.get(f"{BASE_URL}/api/agents")
        agents = agents_response.json()
        author_id = agents[0]["agent_id"]

        # 批量创建条目 (使用信号量限制并发，适配SQLite单写特性)
        batch_sizes = [50, 100, 200]
        semaphore = asyncio.Semaphore(20)  # 限制20并发写入

        for batch_size in batch_sizes:
            start = time.perf_counter()

            async def create_entry(idx):
                async with semaphore:
                    try:
                        large_content = {"data": list(range(50)), "meta": f"entry_{idx}"}
                        resp = await client.post(
                            f"{BASE_URL}/api/entries?author_id={author_id}",
                            json={"topic": "throughput_test", "content": large_content, "tags": ["bench"]}
                        )
                        return resp.status_code
                    except Exception:
                        return 0

            tasks = [create_entry(i) for i in range(batch_size)]
            results = await asyncio.gather(*tasks)
            elapsed = time.perf_counter() - start

            success_count = sum(1 for r in results if r == 201)
            throughput = success_count / elapsed if elapsed > 0 else 0

            print(f"  批量创建 {batch_size} 条条目 (并发限制=20):")
            print(f"    总耗时: {elapsed*1000:.2f}ms")
            print(f"    成功数: {success_count}/{batch_size}")
            print(f"    吞吐量: {throughput:.1f} entries/s")

        # 批量查询 (查询并发不受SQLite写锁限制)
        for batch_size in [100, 500]:
            start = time.perf_counter()
            for _ in range(batch_size):
                await client.get(f"{BASE_URL}/api/entries?topic=throughput_test")
            elapsed = time.perf_counter() - start
            print(f"  批量查询 {batch_size} 次:")
            print(f"    总耗时: {elapsed*1000:.2f}ms")
            print(f"    吞吐量: {batch_size/elapsed:.1f} queries/s")

        # 报告建议的批量 API：一次请求、一次数据库提交。
        batch_size = 200
        payload = {
            "entries": [
                {
                    "topic": "batch-api-performance",
                    "content": {"index": i, "data": "x" * 100},
                }
                for i in range(batch_size)
            ]
        }
        start = time.perf_counter()
        response = await client.post(
            f"{BASE_URL}/api/entries/batch",
            params={"author_id": author_id},
            json=payload,
        )
        elapsed = time.perf_counter() - start
        if response.status_code != 201:
            raise RuntimeError(
                f"批量 API 失败: status={response.status_code}, body={response.text[:300]}"
            )
        created = response.json()
        if len(created) != batch_size:
            raise RuntimeError(
                f"批量 API 返回数量错误: {len(created)}/{batch_size}"
            )
        print(f"  批量 API 创建 {batch_size} 条:")
        print(f"    总耗时: {elapsed*1000:.2f}ms")
        print(f"    吞吐量: {batch_size/elapsed:.1f} entries/s")


async def test_resource_usage():
    """测试资源占用"""
    print("\n" + "=" * 60)
    print("4. 资源占用测试")
    print("=" * 60)

    tracemalloc.start()

    async with create_client() as client:
        agents_response = await client.get(f"{BASE_URL}/api/agents")
        agents = agents_response.json()
        author_id = agents[0]["agent_id"]

        # 执行一系列操作
        for i in range(50):
            await client.post(f"{BASE_URL}/api/agents",
                              json={"name": f"ResourceAgent_{i}", "capabilities": ["test"]})

            await client.post(
                f"{BASE_URL}/api/tasks",
                json={"title": f"ResourceTask_{i}", "required_capabilities": ["test"]}
            )

            await client.post(
                f"{BASE_URL}/api/entries?author_id={author_id}",
                json={"topic": "resource_test", "content": {"i": i}}
            )

            await client.get(f"{BASE_URL}/api/stats")

        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        print(f"  内存占用 (tracemalloc):")
        print(f"    当前: {current / 1024 / 1024:.2f} MB")
        print(f"    峰值: {peak / 1024 / 1024:.2f} MB")

        # 获取系统统计
        stats_response = await client.get(f"{BASE_URL}/api/stats")
        stats = stats_response.json()
        print(f"\n  系统最终状态:")
        print(f"    智能体总数: {stats['total_agents']}")
        print(f"    任务总数: {stats['total_tasks']}")
        print(f"    条目总数: {stats['total_entries']}")
        print(f"    规则总数: {stats['total_rules']}")


async def test_websocket_performance():
    """测试WebSocket实时推送性能"""
    print("\n" + "=" * 60)
    print("5. WebSocket实时推送性能测试")
    print("=" * 60)

    try:
        import websockets

        async with websockets.connect(WS_URL) as ws:
            # 订阅所有任务事件
            await ws.send(json.dumps({"action": "subscribe", "channels": ["task.created", "tasks"]}))
            await ws.recv()  # 确认订阅

            # 发送100个任务创建请求，测量WebSocket推送延迟
            async with create_client() as client:
                agents_response = await client.get(f"{BASE_URL}/api/agents")
                agents = agents_response.json()
                creator_id = agents[0]["agent_id"]

                latencies = []
                for i in range(50):
                    request_time = time.perf_counter()
                    await client.post(
                        f"{BASE_URL}/api/tasks",
                        json={"title": f"WS_Perf_{i}", "required_capabilities": ["python"],
                              "creator_id": creator_id}
                    )
                    # 等待WebSocket推送
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=2.0)
                        push_time = time.perf_counter()
                        latencies.append((push_time - request_time) * 1000)
                    except asyncio.TimeoutError:
                        pass

                if latencies:
                    print(f"  WebSocket推送延迟 ({len(latencies)}次):")
                    print(f"    平均: {statistics.mean(latencies):.2f}ms")
                    print(f"    最小: {min(latencies):.2f}ms")
                    print(f"    最大: {max(latencies):.2f}ms")
                    print(f"    P95:  {sorted(latencies)[int(len(latencies)*0.95)]:.2f}ms")
                else:
                    raise RuntimeError("未收到 WebSocket 推送")

    except ImportError:
        raise RuntimeError("websockets 库未安装，无法完成性能测试")
    except Exception as e:
        print(f"  WebSocket测试异常: {e}")
        raise


async def main():
    print("=" * 60)
    print("智能体动态协作黑板系统 - 性能测试")
    print(f"测试时间: {datetime.now().isoformat()}")
    print("=" * 60)

    try:
        await test_single_request_performance()
        await test_concurrent_performance()
        await test_data_throughput()
        await test_resource_usage()
        await test_websocket_performance()
    except Exception as e:
        print(f"测试异常: {e}")
        import traceback
        traceback.print_exc()
        raise

    print("\n" + "=" * 60)
    print("性能测试完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
