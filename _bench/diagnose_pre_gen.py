"""Diagnose where the app's pre-generation second goes.

Runs stream_assistant_reply in-process against the bench DB and real Ollama,
wrapping each stage with timers:
  list_models — the /api/tags preflight
  middle      — persona + history + retrieval + prompt build (between
                preflight return and the chat_stream call)
  first_chunk — connect + POST + queue + prefill + first content chunk
"""

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import load_settings  # noqa: E402
from backend.app.db.connection import db_connection  # noqa: E402
from backend.app.db.utils import new_id, utc_now  # noqa: E402
from backend.app.services.chat_service import (  # noqa: E402
    insert_message,
    stream_assistant_reply,
)
from backend.app.services.ollama_client import OllamaClient  # noqa: E402

DB_PATH = ROOT / "_bench" / "bench_a.db"
PROMPT = "In exactly one short sentence, what is a memory?"


async def main() -> int:
    settings = load_settings()
    ollama = OllamaClient(settings)

    timings = {"list": [], "middle": [], "first_chunk": [], "total": []}

    list_state = {"t0": None, "t1": None}
    orig_list = ollama.list_models

    async def timed_list():
        start = time.perf_counter()
        result = await orig_list()
        list_state["t0"] = start
        list_state["t1"] = time.perf_counter()
        return result

    ollama.list_models = timed_list

    stream_state = {"called": None, "first_content": None}
    orig_chat_stream = ollama.chat_stream

    async def timed_chat_stream(model, messages, num_ctx):
        stream_state["called"] = time.perf_counter()
        async for chunk in orig_chat_stream(model, messages, num_ctx):
            content = (chunk.get("message") or {}).get("content") or ""
            if content and stream_state["first_content"] is None:
                stream_state["first_content"] = time.perf_counter()
            yield chunk

    ollama.chat_stream = timed_chat_stream

    for trial in range(3):
        conversation_id = new_id()
        now = utc_now()
        with db_connection(DB_PATH) as conn:
            conn.execute(
                "INSERT INTO conversations (id, title, status, created_at, updated_at) "
                "VALUES (?, 'diagnose', 'active', ?, ?)",
                (conversation_id, now, now),
            )
            insert_message(conn, conversation_id, "user", PROMPT)

        list_state.update(t0=None, t1=None)
        stream_state.update(called=None, first_content=None)
        t_start = time.perf_counter()
        with db_connection(DB_PATH) as conn:
            async for event in stream_assistant_reply(
                conn, ollama, settings, conversation_id, None
            ):
                if event["type"] == "done" or event["type"] == "error":
                    break
        t_end = time.perf_counter()

        list_s = (list_state["t1"] - list_state["t0"]) if list_state["t0"] else None
        middle_s = (
            (stream_state["called"] - list_state["t1"])
            if (list_state["t1"] and stream_state["called"])
            else None
        )
        chunk_s = (
            (stream_state["first_content"] - stream_state["called"])
            if (stream_state["called"] and stream_state["first_content"])
            else None
        )
        print(
            f"trial {trial + 1}: preflight={list_s and round(list_s, 3)}s "
            f"middle={middle_s and round(middle_s, 3)}s "
            f"chat_stream_to_first_content={chunk_s and round(chunk_s, 3)}s "
            f"stream_wall={round(t_end - t_start, 3)}s"
        )
        if list_s:
            timings["list"].append(list_s)
        if middle_s:
            timings["middle"].append(middle_s)
        if chunk_s:
            timings["first_chunk"].append(chunk_s)
        await asyncio.sleep(1.0)

    print(
        "medians:",
        {k: (round(sorted(v)[len(v) // 2], 3) if v else None) for k, v in timings.items()},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
