"""Interleaved A/B test: Ollama CPU thread count effect on decode speed.

Decode speed here drifts by ~1 tok/s between blocks (thermals / background
work), which is the same size as the effect being measured. So the two configs
are alternated A,B,A,B... and each trial's difference is computed within its
pair, which cancels drift.

Switching options makes Ollama rebuild the runner (a model load), so each
switch pays a load; the load is reported but excluded from the warm decode
numbers (one warm-up request per switch).

Usage: python _bench/p2_thread_ab.py --pairs 5
"""

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import load_settings  # noqa: E402

PROMPT = (
    "Describe, in two short paragraphs, the sound of rain on an old tin roof "
    "and what it makes someone remember."
)
NUM_PREDICT = 120

A = {"num_ctx": 8192}                      # current production options
B = {"num_ctx": 8192, "num_thread": 8}     # candidate


def run_trial(base_url: str, model: str, messages, options: dict) -> dict:
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": dict(options, num_predict=NUM_PREDICT),
        "keep_alive": "30m",
        "think": False,
    }
    t0 = time.perf_counter()
    ttft = None
    stats = {}
    with httpx.Client(timeout=300.0) as client:
        with client.stream("POST", f"{base_url}/api/chat", json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if (chunk.get("message") or {}).get("content") and ttft is None:
                    ttft = time.perf_counter() - t0
                if chunk.get("done"):
                    stats = chunk
    eval_s = stats.get("eval_duration", 0) / 1e9
    return {
        "decode_tok_s": (stats.get("eval_count", 0) / eval_s) if eval_s else None,
        "output_tokens": stats.get("eval_count"),
        "ttft": ttft,
        "load_s": stats.get("load_duration", 0) / 1e9,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=int, default=5)
    parser.add_argument("--out", default="p2_thread_ab.json")
    args = parser.parse_args()

    settings = load_settings()
    base_url = settings.ollama_base_url
    messages = [{"role": "user", "content": PROMPT}]
    rows = []
    out = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "pairs": rows}

    for pair in range(args.pairs):
        for label, options in (("A_default", A), ("B_threads8", B)):
            run_trial(base_url, settings.model_tag, messages, options)  # warm-up / reload
            trial = run_trial(base_url, settings.model_tag, messages, options)
            print(
                f"  pair {pair + 1} {label:12s} decode={trial['decode_tok_s']:.2f} tok/s "
                f"(load={trial['load_s']:.2f}s ttft={trial['ttft']:.2f}s)"
            )
            if label == "A_default":
                rows.append({"pair": pair + 1, "a": trial})
            else:
                rows[-1]["b"] = trial

    diffs = [
        r["b"]["decode_tok_s"] - r["a"]["decode_tok_s"]
        for r in rows
        if r.get("a") and r.get("b")
    ]
    a_vals = [r["a"]["decode_tok_s"] for r in rows if r.get("a")]
    b_vals = [r["b"]["decode_tok_s"] for r in rows if r.get("b")]
    out["summary"] = {
        "a_median": round(statistics.median(a_vals), 3),
        "b_median": round(statistics.median(b_vals), 3),
        "paired_diff_median": round(statistics.median(diffs), 3),
        "paired_diffs": [round(d, 3) for d in diffs],
        "b_wins": sum(1 for d in diffs if d > 0),
        "pairs": len(diffs),
    }
    print(json.dumps(out["summary"], indent=2))
    (ROOT / "_bench" / args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
