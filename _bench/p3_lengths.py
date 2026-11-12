"""Phase 3 benchmark: reply length and responsiveness per prompt category.

Each trial goes through the real FastAPI SSE endpoint (not a direct Ollama
call), so the numbers include the app's prompt assembly, streaming, and
memory-retrieval work. Token counts come from Ollama's own telemetry, which
the backend writes to its log line ("Generation timing: ... output_tokens=N"),
parsed per trial.

Every category uses a fresh conversation, except `context`, whose history is
seeded identically for both configurations so the comparison is fair.

Usage:
  python _bench/p3_lengths.py --label default --log _bench/p3_default.log \
      --db _bench/bench_p3.db --base-url http://127.0.0.1:8001 --trials 3
"""

import argparse
import json
import re
import sqlite3
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROMPTS = {
    "casual": "hey, how's it going?",
    "emotional": (
        "I'm completely drained. Work has been brutal this month and I feel like "
        "nobody on my team actually listens to me."
    ),
    "technical": (
        "What is a database index, and when does it help versus hurt? "
        "A short example would help."
    ),
    "reasoning": (
        "I've been offered a better-paying job, but taking it means moving away "
        "from my family. How should I think about this?"
    ),
    "context": "So what should I do about the parts we talked about?",
}

# Fixed history for the `context` category (no generation, so both runs see the
# exact same tokens). Two exchanges about an upcoming trip.
SEED_HISTORY = [
    ("user", "I'm planning a two-week trip to Kerala in December with my sister."),
    (
        "assistant",
        "That sounds like a good trip to plan slowly. December is drier in Kerala, "
        "so coastal time is easier, but the hill stations get cold at night. What "
        "are you both hoping to get out of the two weeks?",
    ),
    (
        "user",
        "She wants beaches and I want the tea hills and maybe a backwater boat. "
        "We only have one budget of about 80k rupees though.",
    ),
    (
        "assistant",
        "Then splitting the trip in two halves keeps both of you happy: Kochi and "
        "the backwaters first, then a short climb to Munnar. On 80k for two weeks "
        "for two people, transport and homestays are where the money goes fastest.",
    ),
]

TIMING_RE = re.compile(
    r"Generation timing: ttft_ms=(?P<ttft>[\d.]+) prefill_ms=(?P<prefill>[\d.]+) "
    r"decode_tok_per_s=(?P<decode>[\w.]+) load_ms=(?P<load>[\d.]+) "
    r"prompt_tokens=(?P<prompt>\d+) output_tokens=(?P<output>\d+) total_ms=(?P<total>[\d.]+)"
)
SEED_GAP_S = 2.0


def last_timing(log_path: Path) -> dict | None:
    """Newest backend timing line (the reply this trial just produced)."""
    if not log_path.is_file():
        return None
    matches = list(TIMING_RE.finditer(log_path.read_text(encoding="utf-8", errors="replace")))
    return matches[-1].groupdict() if matches else None


def seed_conversation(db_path: Path, conversation_id: str) -> None:
    """Insert the fixed history directly: identical prompt tokens, no generation."""
    now = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(db_path) as conn:
        for index, (role, content) in enumerate(SEED_HISTORY):
            conn.execute(
                """INSERT INTO messages (id, conversation_id, role, content, created_at,
                                         model_tag, generation_status)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    f"seed-{conversation_id}-{index}",
                    conversation_id,
                    role,
                    content,
                    now,
                    "seed" if role == "assistant" else None,
                    "complete" if role == "assistant" else None,
                ),
            )
        conn.commit()


def create_conversation(client: httpx.Client, base_url: str) -> str:
    response = client.post(f"{base_url}/api/conversations", json={})
    response.raise_for_status()
    return response.json()["id"]


def send_and_measure(client: httpx.Client, base_url: str, conversation_id: str, content: str) -> dict:
    t0 = time.perf_counter()
    ttft = None
    text = ""
    deltas = 0
    error = None
    with client.stream(
        "POST",
        f"{base_url}/api/conversations/{conversation_id}/messages",
        json={"content": content},
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[len("data: "):])
            if event.get("type") == "delta":
                if ttft is None:
                    ttft = time.perf_counter() - t0
                deltas += 1
                text += event.get("content", "")
            elif event.get("type") == "done":
                total = time.perf_counter() - t0
                return {"ttft": ttft, "total": total, "deltas": deltas, "text": text, "error": None}
            elif event.get("type") == "error":
                error = event.get("message")
    return {"ttft": ttft, "total": time.perf_counter() - t0, "deltas": deltas, "text": text, "error": error}


def summarize(values) -> dict:
    clean = [v for v in values if v is not None]
    if not clean:
        return {}
    return {"median": round(statistics.median(clean), 2), "min": round(min(clean), 2),
            "max": round(max(clean), 2), "n": len(clean)}


def sentence_end_ratio(text: str) -> float:
    """Rough completeness signal: does the text end on terminal punctuation?"""
    stripped = text.strip()
    if not stripped:
        return 0.0
    return 1.0 if stripped[-1] in ".!?\"'”’)" else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--log", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    log_path = ROOT / args.log
    db_path = ROOT / args.db
    report = {"label": args.label, "when": time.strftime("%Y-%m-%d %H:%M:%S"),
              "trials": args.trials, "categories": {}}

    with httpx.Client(timeout=300.0) as client:
        # Warm the model so this run measures generation, not a cold load.
        warm_id = create_conversation(client, args.base_url)
        send_and_measure(client, args.base_url, warm_id, "Say the single word: ready.")
        time.sleep(1.0)

        for name, prompt in PROMPTS.items():
            trials = []
            for i in range(args.trials):
                conversation_id = create_conversation(client, args.base_url)
                if name == "context":
                    seed_conversation(db_path, conversation_id)
                measured = send_and_measure(client, args.base_url, conversation_id, prompt)
                time.sleep(0.3)  # let the timing line flush
                timing = last_timing(log_path) or {}
                entry = {
                    "ttft": measured["ttft"],
                    "total": measured["total"],
                    "chars": len(measured["text"]),
                    "words": len(measured["text"].split()),
                    "deltas": measured["deltas"],
                    "ends_clean": sentence_end_ratio(measured["text"]),
                    "error": measured["error"],
                    "output_tokens": int(timing["output"]) if timing.get("output") else None,
                    "prompt_tokens": int(timing["prompt"]) if timing.get("prompt") else None,
                    "decode_tok_s": float(timing["decode"]) if timing.get("decode", "").replace(".", "").isdigit() else None,
                    "prefill_s": (float(timing["prefill"]) / 1000) if timing.get("prefill") else None,
                    "load_s": (float(timing["load"]) / 1000) if timing.get("load") else None,
                    "text": measured["text"],
                }
                trials.append(entry)
                print(
                    f"  [{name} #{i + 1}] ttft={entry['ttft']:.2f}s total={entry['total']:.2f}s "
                    f"out_tok={entry['output_tokens']} words={entry['words']} "
                    f"decode={entry['decode_tok_s']} load={entry['load_s']}s"
                )
                time.sleep(SEED_GAP_S)
            report["categories"][name] = {
                "prompt": prompt,
                "ttft": summarize([t["ttft"] for t in trials]),
                "total": summarize([t["total"] for t in trials]),
                "output_tokens": summarize([t["output_tokens"] for t in trials]),
                "words": summarize([t["words"] for t in trials]),
                "decode_tok_s": summarize([t["decode_tok_s"] for t in trials]),
                "prefill_s": summarize([t["prefill_s"] for t in trials]),
                "load_s": summarize([t["load_s"] for t in trials]),
                "ends_clean_rate": round(sum(t["ends_clean"] for t in trials) / len(trials), 2),
                "trials": trials,
            }

    out = ROOT / "_bench" / (args.out or f"p3_{args.label}.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
