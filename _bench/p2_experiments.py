"""Phase 2 isolated option experiments (one variable at a time).

Every config is tested with the SAME prompt and the SAME generation length, so
each config's numbers are comparable. Each config uses a different Ollama
runner (options differ), so the first request pays a model load; that load is
reported separately and never mixed into the warm numbers.

Usage: python _bench/p2_experiments.py [--trials 3] [--out name.json]
"""

import argparse
import json
import statistics
import subprocess
import threading
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(ROOT))

from backend.app.config import load_settings  # noqa: E402

PROMPT = (
    "Describe, in two short paragraphs, the sound of rain on an old tin roof "
    "and what it makes someone remember."
)
NUM_PREDICT = 100

CONFIGS = {
    "baseline": {"num_ctx": 8192},
    "threads_8": {"num_ctx": 8192, "num_thread": 8},
    "threads_14": {"num_ctx": 8192, "num_thread": 14},
    "threads_20": {"num_ctx": 8192, "num_thread": 20},
    "ctx_4096": {"num_ctx": 4096},
    "batch_1024": {"num_ctx": 8192, "num_batch": 1024},
}


class GpuSampler:
    """Poll nvidia-smi in a thread so utilization during decoding is captured."""

    def __init__(self) -> None:
        self.samples: list[tuple[float, float]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                out = subprocess.run(
                    [
                        "nvidia-smi",
                        "--query-gpu=utilization.gpu,memory.used",
                        "--format=csv,noheader,nounits",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=5,
                ).stdout.strip()
                util, used = [float(p) for p in out.split(",")[:2]]
                self.samples.append((util, used))
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(0.4)

    def __enter__(self) -> "GpuSampler":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=3)

    def summary(self) -> dict:
        if not self.samples:
            return {}
        utils = [s[0] for s in self.samples]
        used = [s[1] for s in self.samples]
        return {
            "gpu_util_median": round(statistics.median(utils), 1),
            "gpu_util_max": round(max(utils), 1),
            "vram_used_max_mib": round(max(used), 0),
        }


def ps_state() -> dict:
    try:
        models = httpx.get("http://127.0.0.1:11434/api/ps", timeout=10).json()["models"]
    except Exception:  # noqa: BLE001
        return {}
    if not models:
        return {"resident": False}
    model = models[0]
    size = model.get("size", 0)
    vram = model.get("size_vram", 0)
    return {
        "resident": True,
        "size_gb": round(size / 1e9, 2),
        "size_vram_gb": round(vram / 1e9, 2),
        "gpu_share_pct": round(vram / size * 100, 1) if size else None,
        "context": model.get("context_length"),
    }


def one_trial(base_url: str, model: str, messages, options: dict) -> dict:
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
            if response.status_code != 200:
                return {"error": f"HTTP {response.status_code}"}
            for line in response.iter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if (chunk.get("message") or {}).get("content") and ttft is None:
                    ttft = time.perf_counter() - t0
                if chunk.get("done"):
                    stats = chunk
    total = time.perf_counter() - t0
    eval_s = stats.get("eval_duration", 0) / 1e9
    prompt_s = stats.get("prompt_eval_duration", 0) / 1e9
    return {
        "ttft": ttft,
        "total": total,
        "prompt_tokens": stats.get("prompt_eval_count"),
        "output_tokens": stats.get("eval_count"),
        "prefill_s": prompt_s,
        "decode_s": eval_s,
        "load_s": stats.get("load_duration", 0) / 1e9,
        "decode_tok_s": (stats.get("eval_count", 0) / eval_s) if eval_s else None,
        "prefill_tok_s": (
            (stats.get("prompt_eval_count", 0) / prompt_s) if prompt_s else None
        ),
    }


def summarize(values) -> dict:
    clean = [v for v in values if v is not None]
    if not clean:
        return {}
    return {
        "median": round(statistics.median(clean), 3),
        "min": round(min(clean), 3),
        "max": round(max(clean), 3),
        "n": len(clean),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--out", default="p2_experiments.json")
    parser.add_argument("--configs", default=",".join(CONFIGS))
    args = parser.parse_args()

    settings = load_settings()
    base_url = settings.ollama_base_url
    messages = [{"role": "user", "content": PROMPT}]
    report = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "model": settings.model_tag,
        "prompt_chars": len(PROMPT),
        "num_predict": NUM_PREDICT,
        "configs": {},
    }

    for name in [c for c in args.configs.split(",") if c]:
        options = CONFIGS[name]
        print(f"== {name}: {options} ==")
        # Warm-up request: pays any reload for this option set.
        with GpuSampler() as sampler:
            warmup = one_trial(base_url, settings.model_tag, messages, options)
        state = ps_state()
        print(
            f"  warmup load={warmup.get('load_s', 0):.2f}s "
            f"decode={warmup.get('decode_tok_s') and round(warmup['decode_tok_s'], 2)} tok/s "
            f"| {state}"
        )
        trials = []
        sampler_all = GpuSampler()
        with sampler_all:
            for i in range(args.trials):
                trial = one_trial(base_url, settings.model_tag, messages, options)
                trials.append(trial)
                print(
                    f"  #{i + 1} ttft={trial.get('ttft') and round(trial['ttft'], 3)}s "
                    f"prefill={trial.get('prefill_s') and round(trial['prefill_s'], 3)}s "
                    f"decode={trial.get('decode_tok_s') and round(trial['decode_tok_s'], 2)} tok/s "
                    f"total={trial.get('total') and round(trial['total'], 2)}s"
                )
                time.sleep(0.5)
        report["configs"][name] = {
            "options": options,
            "warmup_load_s": round(warmup.get("load_s") or 0, 3),
            "placement": state,
            "decode_tok_s": summarize([t.get("decode_tok_s") for t in trials]),
            "prefill_tok_s": summarize([t.get("prefill_tok_s") for t in trials]),
            "prefill_s": summarize([t.get("prefill_s") for t in trials]),
            "ttft": summarize([t.get("ttft") for t in trials]),
            "total": summarize([t.get("total") for t in trials]),
            "gpu": sampler_all.summary(),
            "raw": trials,
        }

    out = ROOT / "_bench" / args.out
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
