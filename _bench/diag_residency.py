"""Residency / placement diagnostic (Phase 2).

Sends ONE short request using the app's own OllamaClient.build_payload so the
request carries exactly the keep_alive / think / num_ctx the app sends, then
prints Ollama's reported residency (`ollama ps`) and the GPU memory snapshot.

Usage:
    python _bench/diag_residency.py            # load + report
    python _bench/diag_residency.py --ps-only  # report current state only
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.config import load_settings  # noqa: E402

PROMPT = "Reply with the single word: ready."


def snapshot(label: str) -> None:
    print(f"--- {label} ---")
    for cmd in (
        ["ollama", "ps"],
        [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader",
        ],
    ):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            print(res.stdout.strip() or res.stderr.strip()[:200])
        except Exception as exc:  # noqa: BLE001
            print(f"{cmd[0]} unavailable: {exc.__class__.__name__}")


def ps_json() -> list[dict]:
    """`/api/ps` parsed, so UNTIL / size_vram are machine-readable."""
    try:
        res = httpx.get("http://127.0.0.1:11434/api/ps", timeout=10)
        return res.json().get("models", [])
    except Exception:  # noqa: BLE001
        return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ps-only", action="store_true")
    parser.add_argument("--num-predict", type=int, default=8)
    args = parser.parse_args()

    settings = load_settings()
    if args.ps_only:
        snapshot("now")
        print(json.dumps(ps_json(), indent=2))
        return 0

    from backend.app.services.ollama_client import OllamaClient

    client = OllamaClient(settings)
    messages = [{"role": "user", "content": PROMPT}]
    payload = client.build_payload(
        settings.model_tag, messages, settings.num_ctx, stream=True
    )
    payload["options"]["num_predict"] = args.num_predict
    print(f"keep_alive={payload.get('keep_alive')!r} think={payload.get('think')!r} "
          f"options={payload['options']}")

    t0 = time.perf_counter()
    ttft = None
    stats = {}
    text = ""
    with httpx.Client(timeout=300.0) as http:
        with http.stream(
            "POST", f"{settings.ollama_base_url}/api/chat", json=payload
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                content = (chunk.get("message") or {}).get("content") or ""
                if content and ttft is None:
                    ttft = time.perf_counter() - t0
                text += content
                if chunk.get("done"):
                    stats = chunk
    total = time.perf_counter() - t0

    def sec(key: str) -> float:
        return stats.get(key, 0) / 1e9

    print(f"ttft={ttft and round(ttft, 3)}s total={total:.3f}s chars={len(text)}")
    print(
        f"load={sec('load_duration'):.3f}s "
        f"prefill={sec('prompt_eval_duration'):.3f}s ({stats.get('prompt_eval_count')} tok, "
        f"{(stats.get('prompt_eval_count', 0) / sec('prompt_eval_duration') if sec('prompt_eval_duration') else 0):.1f} tok/s) "
        f"decode={sec('eval_duration'):.3f}s ({stats.get('eval_count')} tok, "
        f"{(stats.get('eval_count', 0) / sec('eval_duration') if sec('eval_duration') else 0):.2f} tok/s)"
    )
    snapshot("after request")
    models = ps_json()
    for model in models:
        size = model.get("size", 0)
        vram = model.get("size_vram", 0)
        print(
            f"size={size / 1e9:.2f}GB size_vram={vram / 1e9:.2f}GB "
            f"gpu_share={(vram / size * 100 if size else 0):.1f}% "
            f"context={model.get('context_length')} expires_at={model.get('expires_at')}"
        )
        try:
            from datetime import datetime

            exp = datetime.fromisoformat(model["expires_at"].replace("Z", "+00:00"))
            now = datetime.now(exp.tzinfo)
            print(f"residency remaining={ (exp - now).total_seconds() / 60:.1f} min")
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
