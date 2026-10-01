"""Create one agent, one task and one blackboard entry against a running service."""
from __future__ import annotations

import argparse
import asyncio
import os

import httpx


async def run(base_url: str) -> None:
    api_key = os.getenv("BLACKBOARD_API_KEY", "")
    headers = {"X-API-Key": api_key} if api_key else {}
    async with httpx.AsyncClient(base_url=base_url.rstrip("/"), headers=headers, timeout=15) as client:
        agent = await client.post(
            "/api/agents",
            json={"name": "QuickstartAgent", "capabilities": ["python", "coordination"]},
        )
        agent.raise_for_status()
        agent_id = agent.json()["agent_id"]

        task = await client.post(
            "/api/tasks",
            json={
                "title": "Validate the blackboard collaboration loop",
                "required_capabilities": ["python"],
                "priority": "high",
                "creator_id": agent_id,
            },
        )
        task.raise_for_status()

        entry = await client.post(
            "/api/entries",
            params={"author_id": agent_id},
            json={
                "topic": "quickstart",
                "content": {"status": "ready", "pattern": "blackboard"},
                "tags": ["example", "getting-started"],
            },
        )
        entry.raise_for_status()

        stats = await client.get("/api/stats")
        stats.raise_for_status()

    print(f"Agent: {agent_id}")
    print(f"Task status: {task.json()['status']}")
    print(f"Entry: {entry.json()['entry_id']}")
    print(f"Stats: {stats.json()}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.getenv("BLACKBOARD_BASE_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args()
    asyncio.run(run(args.base_url))


if __name__ == "__main__":
    main()
