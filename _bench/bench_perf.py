"""Reproducible local performance benchmark for the companion.

Phases (pick with --phase):
  direct     — one streamed /api/chat per trial through the app's own
               OllamaClient.build_payload (same payload shape the app sends),
               capturing Ollama's own telemetry: load_duration,
               prompt_eval_count/duration, eval_count/duration.
  app        — one POST through the real backend SSE endpoint, capturing
               event timings (user_message / assistant_started / first delta /
               done) and the number of streamed delta frames.
  cold       — unload the model, then one direct request (model load measured).
  components — latency of the pre-generation backend work: /api/tags preflight,
               memory retrieval + prompt assembly against the bench DB.
  contention — send message A, then message B 0.5s after A completes while
               memory extraction from A is pending (measures B's delay).

Usage: python _bench/bench_perf.py --phase direct --trials 5
"""

import argparse
import asyncio
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import load_settings  # noqa: E402

APP = "http://127.0.0.1:8001"
DB_PATH = ROOT / "_bench" / "bench_a.db"


def ollama_base() -> str:
    """The effective configured Ollama base URL (what the app actually uses)."""
    return effective_settings().ollama_base_url

PROMPTS = {
    "short": "In exactly one short sentence, what is a memory?",
    "medium": "Suggest a simple dinner idea using chickpeas, explained in about five sentences.",
    "long": (
        "Write a diary-style paragraph of about 150 words about starting a new "
        "job at a small architecture firm."
    ),
}

# Gap between app-mode trials: long enough for the previous reply's background
# memory extraction to start, so each trial arrives while extraction may be in
# flight (the realistic consecutive-message condition). Same for every run.
APP_TRIAL_GAP_S = 2.0


def effective_settings():
    return load_settings()


def system_prompt_for_bench() -> str:
    """The app's real system prompt for a fresh conversation (persona + empty
    memory section), built with the app's own builder against the bench DB."""
    from backend.app.db.connection import db_connection
    from backend.app.services.persona_service import get_persona
    from backend.app.services.prompt_builder import build_system_prompt

    with db_connection(DB_PATH) as conn:
        persona = get_persona(conn)
    return build_system_prompt(persona, [], memory_section=True)


def build_messages_for(prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt_for_bench()},
        {"role": "user", "content": prompt},
    ]


async def direct_trial(client: httpx.AsyncClient, settings, messages) -> dict:
    """Stream one chat request straight to Ollama with the app's payload."""
    from backend.app.services.ollama_client import OllamaClient

    payload = OllamaClient(settings).build_payload(
        settings.model_tag, messages, settings.num_ctx, stream=True
    )
    t0 = time.perf_counter()
    ttft = None
    total = None
    stats = {}
    chars = 0
    frames = 0
    async with client.stream("POST", f"{ollama_base()}/api/chat", json=payload) as response:
        if response.status_code != 200:
            body = await response.aread()
            return {"error": f"HTTP {response.status_code}: {body[:200]!r}"}
        async for line in response.aiter_lines():
            if not line.strip():
                continue
            frames += 1
            try:
                chunk = json.loads(line)
            except ValueError:
                continue
            content = (chunk.get("message") or {}).get("content") or ""
            if content and ttft is None:
                ttft = time.perf_counter() - t0
            chars += len(content)
            if chunk.get("done"):
                total = time.perf_counter() - t0
                for key in (
                    "load_duration",
                    "prompt_eval_count",
                    "prompt_eval_duration",
                    "eval_count",
                    "eval_duration",
                ):
                    if key in chunk:
                        stats[key] = chunk[key]
    eval_s = stats.get("eval_duration", 0) / 1e9
    prompt_s = stats.get("prompt_eval_duration", 0) / 1e9
    load_s = stats.get("load_duration", 0) / 1e9
    return {
        "ttft": ttft,
        "total": total,
        "chars": chars,
        "frames": frames,
        "prompt_tokens": stats.get("prompt_eval_count"),
        "output_tokens": stats.get("eval_count"),
        "prefill_s": prompt_s,
        "gen_s": eval_s,
        "load_s": load_s,
        "tok_per_s": (stats["eval_count"] / eval_s) if eval_s > 0 else None,
        "prefill_tok_per_s": (
            stats["prompt_eval_count"] / prompt_s if prompt_s > 0 else None
        ),
    }


async def app_trial(client: httpx.AsyncClient, conversation_id: str, content: str) -> dict:
    """POST through the real backend and record SSE event timings."""
    t0 = time.perf_counter()
    t_user = t_started = ttft = t_done = None
    deltas = 0
    chars = 0
    error = None
    url = f"{APP}/api/conversations/{conversation_id}/messages"
    async with client.stream("POST", url, json={"content": content}) as response:
        if response.status_code != 200:
            await response.aread()
            return {"error": f"HTTP {response.status_code}"}
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[len("data: "):])
            except ValueError:
                continue
            now = time.perf_counter() - t0
            etype = event.get("type")
            if etype == "user_message":
                t_user = now
            elif etype == "assistant_started":
                t_started = now
            elif etype == "delta":
                deltas += 1
                chars += len(event.get("content", ""))
                if ttft is None:
                    ttft = now
            elif etype == "done":
                t_done = now
            elif etype == "error":
                error = event.get("message")
    return {
        "error": error,
        "first_event_s": t_user,
        "assistant_started_s": t_started,
        "ttft": ttft,
        "total": t_done,
        "deltas": deltas,
        "chars": chars,
        # Estimated from characters (labeled as estimated in reports).
        "est_output_tokens": round(chars / 4),
        "pre_generation_s": (ttft - t_started) if (ttft and t_started) else None,
    }


def summarize(values: list[float | None]) -> dict:
    clean = [v for v in values if v is not None]
    if not clean:
        return {}
    return {
        "median": round(statistics.median(clean), 3),
        "min": round(min(clean), 3),
        "max": round(max(clean), 3),
        "n": len(clean),
    }


def gpu_snapshot() -> dict:
    out = {}
    try:
        res = subprocess.run(
            ["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        out["gpu"] = res.stdout.strip()
    except Exception as exc:  # noqa: BLE001
        out["gpu"] = f"unavailable: {exc.__class__.__name__}"
    try:
        res = subprocess.run(["ollama", "ps"], capture_output=True, text=True, timeout=10)
        out["ollama_ps"] = res.stdout.strip().splitlines()[-1]
    except Exception as exc:  # noqa: BLE001
        out["ollama_ps"] = f"unavailable: {exc.__class__.__name__}"
    return out


async def phase_direct(trials: int) -> dict:
    settings = effective_settings()
    results: dict = {"mode": "direct", "model": settings.model_tag, "num_ctx": settings.num_ctx, "prompts": {}}
    limits = httpx.Limits(max_connections=5)
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), limits=limits) as client:
        for name, prompt in PROMPTS.items():
            messages = build_messages_for(prompt)
            trials_out = []
            for i in range(trials):
                trial = await direct_trial(client, settings, messages)
                trials_out.append(trial)
                print(f"  [{name} #{i+1}] ttft={trial.get('ttft') and round(trial['ttft'],2)}s "
                      f"total={trial.get('total') and round(trial['total'],2)}s "
                      f"tok/s={trial.get('tok_per_s') and round(trial['tok_per_s'],1)} "
                      f"prompt_tok={trial.get('prompt_tokens')} out_tok={trial.get('output_tokens')}")
                await asyncio.sleep(1.0)
            results["prompts"][name] = {
                "ttft": summarize([t.get("ttft") for t in trials_out]),
                "total": summarize([t.get("total") for t in trials_out]),
                "tok_per_s": summarize([t.get("tok_per_s") for t in trials_out]),
                "prefill_s": summarize([t.get("prefill_s") for t in trials_out]),
                "gen_s": summarize([t.get("gen_s") for t in trials_out]),
                "load_s": summarize([t.get("load_s") for t in trials_out]),
                "prompt_tokens": summarize([t.get("prompt_tokens") for t in trials_out]),
                "output_tokens": summarize([t.get("output_tokens") for t in trials_out]),
                "prefill_tok_per_s": summarize([t.get("prefill_tok_per_s") for t in trials_out]),
                "raw": trials_out,
            }
    results["runtime"] = gpu_snapshot()
    return results


async def phase_cold(trials: int) -> dict:
    settings = effective_settings()
    results = {"mode": "cold", "trials": []}
    limits = httpx.Limits(max_connections=5)
    for i in range(trials):
        subprocess.run(["ollama", "stop", settings.model_tag], capture_output=True, timeout=30)
        await asyncio.sleep(2.0)
        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), limits=limits) as client:
            trial = await direct_trial(client, settings, build_messages_for(PROMPTS["short"]))
        print(f"  [cold #{i+1}] ttft={trial.get('ttft') and round(trial['ttft'],2)}s "
              f"load={trial.get('load_s') and round(trial['load_s'],2)}s "
              f"total={trial.get('total') and round(trial['total'],2)}s")
        results["trials"].append(trial)
        await asyncio.sleep(2.0)
    # Reload for subsequent phases.
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        await direct_trial(client, settings, build_messages_for(PROMPTS["short"]))
    results["ttft"] = summarize([t.get("ttft") for t in results["trials"]])
    results["load_s"] = summarize([t.get("load_s") for t in results["trials"]])
    results["total"] = summarize([t.get("total") for t in results["trials"]])
    return results


async def ensure_conversation() -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(f"{APP}/api/conversations", json={})
        response.raise_for_status()
        return response.json()["id"]


async def phase_app(trials: int) -> dict:
    results = {"mode": "app", "prompts": {}}
    limits = httpx.Limits(max_connections=5)
    conversation_id = await ensure_conversation()
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), limits=limits) as client:
        for name, prompt in PROMPTS.items():
            trials_out = []
            for i in range(trials):
                trial = await app_trial(client, conversation_id, prompt)
                trials_out.append(trial)
                print(f"  [{name} #{i+1}] first_evt={trial.get('first_event_s') and round(trial['first_event_s'],3)}s "
                      f"ttft={trial.get('ttft') and round(trial['ttft'],2)}s "
                      f"total={trial.get('total') and round(trial['total'],2)}s "
                      f"deltas={trial.get('deltas')}")
                await asyncio.sleep(APP_TRIAL_GAP_S)
            results["prompts"][name] = {
                "first_event": summarize([t.get("first_event_s") for t in trials_out]),
                "ttft": summarize([t.get("ttft") for t in trials_out]),
                "total": summarize([t.get("total") for t in trials_out]),
                "pre_generation": summarize([t.get("pre_generation_s") for t in trials_out]),
                "deltas": summarize([t.get("deltas") for t in trials_out]),
                "est_output_tokens": summarize([t.get("est_output_tokens") for t in trials_out]),
                "raw": trials_out,
            }
    results["runtime"] = gpu_snapshot()
    return results


async def phase_components(trials: int) -> dict:
    """Latency of the backend's non-model work before generation starts."""
    results = {"mode": "components"}
    limits = httpx.Limits(max_connections=5)
    tag_times = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0), limits=limits) as client:
        for _ in range(trials):
            t0 = time.perf_counter()
            response = await client.get(f"{ollama_base()}/api/tags")
            assert response.status_code == 200
            tag_times.append(time.perf_counter() - t0)
            await asyncio.sleep(0.05)
    results["preflight_tags_ms"] = summarize([t * 1000 for t in tag_times])

    # Steady-state preflight: one persistent OllamaClient (as the app keeps on
    # app.state), so the positive model-list cache is exercised — this is what
    # a chat message actually pays after the first health check. One warm-up
    # call fills the 30s cache before timing begins.
    from backend.app.services.ollama_client import OllamaClient

    cached_client = OllamaClient(effective_settings())
    cached_times = []
    await cached_client.list_models()  # warm-up: populate the cache
    for _ in range(trials):
        t0 = time.perf_counter()
        names = await cached_client.list_models()
        cached_times.append(time.perf_counter() - t0)
        assert cached_client._model_tag in names
        await asyncio.sleep(0.05)
    results["preflight_cached_ms"] = summarize([t * 1000 for t in cached_times])

    # Memory retrieval + prompt assembly against the bench DB (the work the
    # backend does between assistant_started and the Ollama request).
    from backend.app.db.connection import db_connection
    from backend.app.services.chat_service import _retrieve_memories_for_prompt
    from backend.app.services.persona_service import get_persona
    from backend.app.services.prompt_builder import build_messages, build_system_prompt

    settings = effective_settings()
    retrieval_times = []
    build_times = []
    # Seed a conversation + user message directly (no model generation).
    from backend.app.db.utils import new_id, utc_now
    from backend.app.services.chat_service import insert_message

    conversation_id = new_id()
    now = utc_now()
    with db_connection(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, status, created_at, updated_at) "
            "VALUES (?, 'bench', 'active', ?, ?)",
            (conversation_id, now, now),
        )
        insert_message(conn, conversation_id, "user", "What do you remember about my favourite tea?")
    for _ in range(trials):
        with db_connection(DB_PATH) as conn:
            t0 = time.perf_counter()
            memories = _retrieve_memories_for_prompt(conn, settings, conversation_id)
            retrieval_times.append(time.perf_counter() - t0)
            persona = get_persona(conn)
            t0 = time.perf_counter()
            build_messages(
                [],
                settings.context_message_count,
                settings.context_max_chars,
                system_prompt=build_system_prompt(persona, memories, memory_section=True),
            )
            build_times.append(time.perf_counter() - t0)
    results["retrieval_ms"] = summarize([t * 1000 for t in retrieval_times])
    results["prompt_build_ms"] = summarize([t * 1000 for t in build_times])
    return results


async def phase_contention(trials: int) -> dict:
    """Second message while extraction from the first is pending."""
    results = {"mode": "contention", "trials": []}
    limits = httpx.Limits(max_connections=5)
    conversation_id = await ensure_conversation()
    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0), limits=limits) as client:
        for i in range(trials):
            a = await app_trial(client, conversation_id, PROMPTS["medium"])
            await asyncio.sleep(0.5)  # extraction from A starts after A completes
            b = await app_trial(client, conversation_id, PROMPTS["short"])
            entry = {
                "a_ttft": a.get("ttft"), "a_total": a.get("total"),
                "b_ttft": b.get("ttft"), "b_total": b.get("total"),
            }
            results["trials"].append(entry)
            print(f"  [contention #{i+1}] A ttft={entry['a_ttft'] and round(entry['a_ttft'],2)}s "
                  f"B ttft={entry['b_ttft'] and round(entry['b_ttft'],2)}s "
                  f"B total={entry['b_total'] and round(entry['b_total'],2)}s")
            await asyncio.sleep(APP_TRIAL_GAP_S)
    results["b_ttft"] = summarize([t["b_ttft"] for t in results["trials"]])
    results["b_total"] = summarize([t["b_total"] for t in results["trials"]])
    return results


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="direct",
                        choices=["direct", "app", "cold", "components", "contention", "all"])
    parser.add_argument("--trials", type=int, default=5)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    phases = ["direct", "app", "cold", "components", "contention"] if args.phase == "all" else [args.phase]
    settings = effective_settings()
    print(f"model={settings.model_tag} num_ctx={settings.num_ctx} keep_alive={settings.ollama_keep_alive} "
          f"think={settings.ollama_think} num_predict={settings.max_output_tokens}")
    print(f"runtime before: {gpu_snapshot()}")

    report = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "phases": {}}
    for phase in phases:
        print(f"== phase: {phase} ({args.trials} trials) ==")
        if phase == "direct":
            report["phases"][phase] = await phase_direct(args.trials)
        elif phase == "app":
            report["phases"][phase] = await phase_app(args.trials)
        elif phase == "cold":
            report["phases"][phase] = await phase_cold(max(1, min(args.trials, 2)))
        elif phase == "components":
            report["phases"][phase] = await phase_components(args.trials)
        elif phase == "contention":
            report["phases"][phase] = await phase_contention(max(1, args.trials // 2))

    print(json.dumps({k: v for k, v in report["phases"].items()}, indent=2, default=str))
    out = Path(args.out) if args.out else ROOT / "_bench" / f"bench_{phases[0]}.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
