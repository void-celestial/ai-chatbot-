"""Phase 3 reply-style comparison, isolated from background extraction.

The app path is measured separately (p3_lengths.py); this harness removes the
one confounder that dominated it: memory extraction from the previous reply
still generating while the next request arrives (observed decode dropping from
~13 to ~5.4 tok/s). It calls Ollama directly with the app's *own* system prompt
(built by build_system_prompt from the bench persona) and the app's options,
so only the reply-style guidance differs between runs.

The style comes from the environment (OLLAMA_REPLY_STYLE), so both arms can be
measured without touching the backend or production config:

    python _bench/p3_styles.py --label default --trials 3
    OLLAMA_REPLY_STYLE=concise python _bench/p3_styles.py --label concise --trials 3
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
from backend.app.db.connection import db_connection  # noqa: E402
from backend.app.services.persona_service import get_persona  # noqa: E402
from backend.app.services.prompt_builder import build_system_prompt  # noqa: E402

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


def system_prompt(settings, db_path: Path) -> str:
    with db_connection(db_path) as conn:
        persona = get_persona(conn)
    return build_system_prompt(
        persona,
        [],
        memory_section=True,
        statement_max_chars=settings.memory_statement_prompt_chars,
        memory_max_chars=settings.memory_prompt_max_chars,
        reply_style=settings.reply_style,
    )


def trial(base_url: str, settings, system: str, category: str) -> dict:
    messages = [{"role": "system", "content": system}]
    if category == "context":
        messages += [{"role": role, "content": content} for role, content in SEED_HISTORY]
    messages.append({"role": "user", "content": PROMPTS[category]})

    options = {"num_ctx": settings.num_ctx}
    if settings.max_output_tokens > 0:
        options["num_predict"] = settings.max_output_tokens
    payload = {
        "model": settings.model_tag,
        "messages": messages,
        "stream": True,
        "options": options,
    }
    if settings.ollama_keep_alive:
        payload["keep_alive"] = settings.ollama_keep_alive
    if settings.ollama_think is not None:
        payload["think"] = settings.ollama_think

    t0 = time.perf_counter()
    ttft = None
    text = ""
    stats = {}
    with httpx.Client(timeout=600.0) as client:
        with client.stream("POST", f"{base_url}/api/chat", json=payload) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                delta = (chunk.get("message") or {}).get("content") or ""
                if delta:
                    if ttft is None:
                        ttft = time.perf_counter() - t0
                    text += delta
                if chunk.get("done"):
                    stats = chunk
    total = time.perf_counter() - t0
    prefill_s = stats.get("prompt_eval_duration", 0) / 1e9
    decode_s = stats.get("eval_duration", 0) / 1e9
    stripped = text.strip()
    return {
        "ttft": ttft,
        "total": total,
        "chars": len(text),
        "words": len(stripped.split()),
        "output_tokens": stats.get("eval_count"),
        "prompt_tokens": stats.get("prompt_eval_count"),
        "prefill_s": prefill_s,
        "decode_s": decode_s,
        "decode_tok_s": (stats.get("eval_count", 0) / decode_s) if decode_s else None,
        "load_s": stats.get("load_duration", 0) / 1e9,
        "finish_reason": stats.get("done_reason"),
        "ends_clean": bool(stripped) and stripped[-1] in ".!?\"'”’)",
        "text": text,
    }


def summarize(values) -> dict:
    clean = [v for v in values if v is not None]
    if not clean:
        return {}
    return {"median": round(statistics.median(clean), 2), "min": round(min(clean), 2),
            "max": round(max(clean), 2), "n": len(clean)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", required=True)
    parser.add_argument("--db", default="_bench/bench_p3_default.db")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--categories", default=",".join(PROMPTS))
    args = parser.parse_args()

    settings = load_settings()
    db_path = ROOT / args.db
    system = system_prompt(settings, db_path)
    print(
        f"reply_style={settings.reply_style} num_predict={settings.max_output_tokens} "
        f"num_ctx={settings.num_ctx} system_chars={len(system)}"
    )
    report = {
        "label": args.label,
        "reply_style": settings.reply_style,
        "max_output_tokens": settings.max_output_tokens,
        "system_prompt_chars": len(system),
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "categories": {},
    }

    for category in [c for c in args.categories.split(",") if c]:
        # Warm-up: settles model residency and prompt-cache state.
        trial(settings.ollama_base_url, settings, system, category)
        results = [
            trial(settings.ollama_base_url, settings, system, category)
            for _ in range(args.trials)
        ]
        for i, r in enumerate(results):
            print(
                f"  [{category} #{i + 1}] ttft={r['ttft']:.2f}s total={r['total']:.2f}s "
                f"out_tok={r['output_tokens']} words={r['words']} "
                f"decode={r['decode_tok_s']:.1f} tok/s reason={r['finish_reason']} "
                f"clean_end={r['ends_clean']}"
            )
        report["categories"][category] = {
            "prompt": PROMPTS[category],
            "output_tokens": summarize([r["output_tokens"] for r in results]),
            "words": summarize([r["words"] for r in results]),
            "ttft": summarize([r["ttft"] for r in results]),
            "total": summarize([r["total"] for r in results]),
            "decode_tok_s": summarize([r["decode_tok_s"] for r in results]),
            "prompt_tokens": summarize([r["prompt_tokens"] for r in results]),
            "finish_reasons": [r["finish_reason"] for r in results],
            "clean_ends": sum(1 for r in results if r["ends_clean"]),
            "trials": results,
        }

    out = ROOT / "_bench" / f"p3_style_{args.label}.json"
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
