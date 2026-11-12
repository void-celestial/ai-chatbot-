"""Interleaved A/B: default num_batch (512) vs 2048, long uncached prompt.

Both metrics are measured in the same trial because the trade-off matters:
a bigger batch speeds prompt prefill but needs a bigger compute buffer, which
was observed to push layers from GPU to CPU (54.6% -> 50.1% of the model in
VRAM) and could slow decoding.

Usage: python _bench/p2_batch_ab.py --pairs 4
"""

import argparse
import json
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import load_settings  # noqa: E402

FILLER = (
    "The committee met on a damp Tuesday to discuss drainage, lamp posts and "
    "the annual budget for the community hall. "
)
TARGET_CHARS = 12_000
NUM_PREDICT = 64

A = {}                       # production: model default batch (512)
B = {"num_batch": 2048}      # candidate


def placement() -> dict:
    try:
        models = httpx.get("http://127.0.0.1:11434/api/ps", timeout=10).json()["models"]
    except Exception:  # noqa: BLE001
        return {}
    if not models:
        return {}
    m = models[0]
    return {"gpu_share_pct": round(m.get("size_vram", 0) / m.get("size", 1) * 100, 1)}


def trial(base_url: str, model: str, num_ctx: int, extra: dict) -> dict:
    nonce = uuid.uuid4().hex
    body = (FILLER * (TARGET_CHARS // len(FILLER) + 1))[:TARGET_CHARS]
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": f"[{nonce}] Summarise this in one sentence: {body}"}],
        "stream": True,
        "options": {"num_ctx": num_ctx, "num_predict": NUM_PREDICT, **extra},
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
    prefill_s = stats.get("prompt_eval_duration", 0) / 1e9
    eval_s = stats.get("eval_duration", 0) / 1e9
    return {
        "prompt_tokens": stats.get("prompt_eval_count"),
        "prefill_s": prefill_s,
        "prefill_tok_s": (stats.get("prompt_eval_count", 0) / prefill_s) if prefill_s else None,
        "decode_tok_s": (stats.get("eval_count", 0) / eval_s) if eval_s else None,
        "ttft": ttft,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs", type=int, default=4)
    parser.add_argument("--out", default="p2_batch_ab.json")
    args = parser.parse_args()

    settings = load_settings()
    rows = []
    for pair in range(args.pairs):
        for label, extra in (("A_default512", A), ("B_batch2048", B)):
            trial(settings.ollama_base_url, settings.model_tag, settings.num_ctx, extra)  # reload
            result = trial(settings.ollama_base_url, settings.model_tag, settings.num_ctx, extra)
            result["placement"] = placement()
            print(
                f"  pair {pair + 1} {label:12s} prefill={result['prefill_tok_s']:6.0f} tok/s "
                f"decode={result['decode_tok_s']:5.2f} tok/s ttft={result['ttft']:.2f}s "
                f"gpu={result['placement'].get('gpu_share_pct')}%"
            )
            if label == "A_default512":
                rows.append({"pair": pair + 1, "a": result})
            else:
                rows[-1]["b"] = result

    def med(key: str, side: str) -> float:
        return round(statistics.median([r[side][key] for r in rows]), 2)

    diffs_prefill = [r["b"]["prefill_tok_s"] - r["a"]["prefill_tok_s"] for r in rows]
    diffs_decode = [r["b"]["decode_tok_s"] - r["a"]["decode_tok_s"] for r in rows]
    summary = {
        "a_prefill_tok_s": med("prefill_tok_s", "a"),
        "b_prefill_tok_s": med("prefill_tok_s", "b"),
        "a_decode_tok_s": med("decode_tok_s", "a"),
        "b_decode_tok_s": med("decode_tok_s", "b"),
        "a_ttft_s": med("ttft", "a"),
        "b_ttft_s": med("ttft", "b"),
        "prefill_paired_diffs": [round(d, 1) for d in diffs_prefill],
        "decode_paired_diffs": [round(d, 2) for d in diffs_decode],
        "prefill_b_wins": sum(1 for d in diffs_prefill if d > 0),
        "decode_b_wins": sum(1 for d in diffs_decode if d > 0),
    }
    print(json.dumps(summary, indent=2))
    (ROOT / "_bench" / args.out).write_text(
        json.dumps({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "summary": summary, "pairs": rows}, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
