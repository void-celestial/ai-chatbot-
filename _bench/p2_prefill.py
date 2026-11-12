"""Prefill (prompt-processing) benchmark with uncached long prompts.

Short repeated prompts are served from Ollama's prompt cache, which hides the
real prefill cost. Each trial here starts with a unique nonce so the prefix
cache always misses, and every config sees the same prompt length.

Measures prompt_eval_count / prompt_eval_duration and reports the placement
Ollama chose, because a larger batch needs a larger compute buffer and can
push layers from GPU to CPU.

Usage: python _bench/p2_prefill.py [--configs default,batch1024,batch2048,batch256]
"""

import argparse
import json
import random
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

CONFIGS = {
    "default": {},
    "batch1024": {"num_batch": 1024},
    "batch2048": {"num_batch": 2048},
    "batch256": {"num_batch": 256},
}

TARGET_CHARS = 12_000


def placement() -> dict:
    try:
        models = httpx.get("http://127.0.0.1:11434/api/ps", timeout=10).json()["models"]
    except Exception:  # noqa: BLE001
        return {}
    if not models:
        return {}
    m = models[0]
    return {
        "size_vram_gb": round(m.get("size_vram", 0) / 1e9, 2),
        "gpu_share_pct": round(m.get("size_vram", 0) / m.get("size", 1) * 100, 1),
    }


def trial(base_url: str, model: str, num_ctx: int, extra: dict) -> dict:
    nonce = uuid.uuid4().hex
    body = (FILLER * (TARGET_CHARS // len(FILLER) + 1))[:TARGET_CHARS]
    messages = [{"role": "user", "content": f"[{nonce}] Summarise this in one sentence: {body}"}]
    payload = {
        "model": model,
        "messages": messages,
        "stream": True,
        "options": {"num_ctx": num_ctx, "num_predict": 16, **extra},
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
    return {
        "prompt_tokens": stats.get("prompt_eval_count"),
        "prefill_s": prefill_s,
        "prefill_tok_s": (stats.get("prompt_eval_count", 0) / prefill_s) if prefill_s else None,
        "ttft": ttft,
        "load_s": stats.get("load_duration", 0) / 1e9,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configs", default=",".join(CONFIGS))
    parser.add_argument("--trials", type=int, default=2)
    parser.add_argument("--out", default="p2_prefill.json")
    args = parser.parse_args()

    settings = load_settings()
    report = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "chars": TARGET_CHARS, "configs": {}}
    for name in [c for c in args.configs.split(",") if c]:
        extra = CONFIGS[name]
        print(f"== {name}: {extra or 'model defaults'}")
        trial(settings.ollama_base_url, settings.model_tag, settings.num_ctx, extra)  # reload
        results = [
            trial(settings.ollama_base_url, settings.model_tag, settings.num_ctx, extra)
            for _ in range(args.trials)
        ]
        rates = [r["prefill_tok_s"] for r in results if r["prefill_tok_s"]]
        report["configs"][name] = {
            "options": extra,
            "placement": placement(),
            "prefill_tok_s_median": round(statistics.median(rates), 1) if rates else None,
            "trials": results,
        }
        for r in results:
            print(
                f"  prompt_tok={r['prompt_tokens']} prefill={r['prefill_s']:.2f}s "
                f"rate={r['prefill_tok_s']:.0f} tok/s ttft={r['ttft']:.2f}s "
                f"load={r['load_s']:.2f}s"
            )
        print(f"  placement: {report['configs'][name]['placement']}")

    (ROOT / "_bench" / args.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
