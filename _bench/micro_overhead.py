"""Isolate where app-vs-direct latency differs.

Interleaves, in rounds, four request patterns against the local Ollama:
  tags_fresh    — new httpx client per GET /api/tags (what OllamaClient does)
  tags_shared   — one shared client, connection reuse
  chat_fresh    — new client, tiny streamed chat, num_predict=1 (what chat_stream does)
  chat_shared   — one shared client, same tiny chat
"""

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OLLAMA = "http://localhost:11434"
ROUNDS = 8

TINY_PAYLOAD = {
    "model": "qwen3.5:9b",
    "messages": [{"role": "user", "content": "Say hi."}],
    "stream": True,
    "options": {"num_ctx": 8192, "num_predict": 1},
    "keep_alive": "30m",
    "think": False,
}


async def tags_fresh() -> float:
    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(f"{OLLAMA}/api/tags")
        assert response.status_code == 200
    return time.perf_counter() - t0


async def chat_fresh(client: httpx.AsyncClient | None = None) -> float:
    t0 = time.perf_counter()
    first = None

    async def run(active: httpx.AsyncClient) -> None:
        nonlocal first
        async with active.stream("POST", f"{OLLAMA}/api/chat", json=TINY_PAYLOAD) as response:
            assert response.status_code == 200
            async for line in response.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                content = (chunk.get("message") or {}).get("content") or ""
                if content and first is None:
                    first = time.perf_counter() - t0

    if client is None:
        async with httpx.AsyncClient(timeout=30.0) as fresh:
            await run(fresh)
    else:
        await run(client)
    return first if first is not None else time.perf_counter() - t0


async def main() -> int:
    results: dict[str, list[float]] = {
        "tags_fresh": [], "tags_shared": [], "chat_fresh": [], "chat_shared": [],
    }
    async with httpx.AsyncClient(timeout=30.0) as shared:
        for round_index in range(ROUNDS):
            results["tags_fresh"].append(await tags_fresh())

            t0 = time.perf_counter()
            response = await shared.get(f"{OLLAMA}/api/tags")
            assert response.status_code == 200
            results["tags_shared"].append(time.perf_counter() - t0)

            results["chat_fresh"].append(await chat_fresh())
            results["chat_shared"].append(await chat_fresh(shared))
            await asyncio.sleep(0.3)

    for name, values in results.items():
        print(
            f"{name:12s} median={statistics.median(values) * 1000:7.1f}ms "
            f"min={min(values) * 1000:7.1f}ms max={max(values) * 1000:7.1f}ms "
            f"samples={[round(v * 1000, 1) for v in values]}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
