"""Phase 4: interleaved three-style comparison.

Styles are prompt-only, so switching them costs no model reload; rotating
`default` / `concise` / `adaptive` inside each round cancels thermal/background
drift that would otherwise bias whichever style ran first.

Prompt sets:
  main     - the Phase 3 categories (casual, emotional, technical, reasoning,
             context), identical prompts and seeded history.
  explicit - explicit user instructions about depth, which the adaptive policy
             must obey: "explain in detail", "keep it short", "give me just the
             answer", "walk me through it".

Usage:
  python _bench/p4_compare.py --set main --categories technical,reasoning --rounds 3
  python _bench/p4_compare.py --set explicit --rounds 3
"""

import argparse
import dataclasses
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

PROMPT_SETS = {
    "main": {
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
    },
    "explicit": {
        "detail": (
            "What is a database index, and when does it help versus hurt? "
            "Explain in detail with an example."
        ),
        "short": (
            "I've been offered a better-paying job, but taking it means moving away "
            "from my family. How should I think about this? Keep it short."
        ),
        "just_answer": "Is it worth putting an index on a boolean column? Give me just the answer.",
        "walk": (
            "Walk me through what happens step by step when I insert a row into a "
            "table that has an index."
        ),
    },
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

STYLES = ("default", "concise", "adaptive")


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


def trial(base_url: str, settings, system: str, prompt: str, category: str) -> dict:
    messages = [{"role": "system", "content": system}]
    if category == "context":
        messages += [{"role": role, "content": content} for role, content in SEED_HISTORY]
    messages.append({"role": "user", "content": prompt})

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
    decode_s = stats.get("eval_duration", 0) / 1e9
    stripped = text.strip()
    return {
        "ttft": ttft,
        "total": total,
        "chars": len(text),
        "words": len(stripped.split()),
        "output_tokens": stats.get("eval_count"),
        "prompt_tokens": stats.get("prompt_eval_count"),
        "decode_tok_s": (stats.get("eval_count", 0) / decode_s) if decode_s else None,
        "finish_reason": stats.get("done_reason"),
        "ends_clean": bool(stripped) and stripped[-1] in ".!?\"'”’)",
        "has_numbered_steps": any(line.strip()[:2] in {f"{n}." for n in range(1, 10)}
                                  for line in text.splitlines()),
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
    parser.add_argument("--set", dest="prompt_set", default="main", choices=sorted(PROMPT_SETS))
    parser.add_argument("--categories", default=None)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--db", default="_bench/bench_p3_default.db")
    parser.add_argument("--styles", default=",".join(STYLES))
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    prompts = PROMPT_SETS[args.prompt_set]
    categories = [c for c in (args.categories.split(",") if args.categories else prompts)]
    styles = tuple(s for s in args.styles.split(",") if s)
    base = load_settings()
    db_path = ROOT / args.db
    variants = {style: dataclasses.replace(base, reply_style=style) for style in styles}
    systems = {style: system_prompt(variants[style], db_path) for style in styles}
    for style in styles:
        print(f"{style}: system_prompt_chars={len(systems[style])}")

    results: dict[str, dict[str, list[dict]]] = {
        category: {style: [] for style in styles} for category in categories
    }

    # Warm-up per style so the first measured trial is not a cold graph build.
    for style in styles:
        trial(base.ollama_base_url, variants[style], systems[style], "Say the single word: ready.", "casual")

    for round_index in range(args.rounds):
        shift = round_index % len(styles)
        order = tuple(list(styles[shift:]) + list(styles[:shift]))
        for category in categories:
            for style in order:
                result = trial(
                    base.ollama_base_url,
                    variants[style],
                    systems[style],
                    prompts[category],
                    category,
                )
                results[category][style].append(result)
                print(
                    f"  r{round_index + 1} [{category:10s} {style:9s}] "
                    f"tok={result['output_tokens']:4d} ttft={result['ttft']:.2f}s "
                    f"total={result['total']:6.2f}s decode={result['decode_tok_s']:.1f} "
                    f"reason={result['finish_reason']} clean={result['ends_clean']}"
                )

    report = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "set": args.prompt_set,
              "rounds": args.rounds, "categories": {}, "system_chars": {k: len(v) for k, v in systems.items()}}
    for category in categories:
        report["categories"][category] = {"prompt": prompts[category], "styles": {}}
        for style in styles:
            trials = results[category][style]
            report["categories"][category]["styles"][style] = {
                "output_tokens": summarize([t["output_tokens"] for t in trials]),
                "words": summarize([t["words"] for t in trials]),
                "ttft": summarize([t["ttft"] for t in trials]),
                "total": summarize([t["total"] for t in trials]),
                "decode_tok_s": summarize([t["decode_tok_s"] for t in trials]),
                "finish_reasons": [t["finish_reason"] for t in trials],
                "clean_ends": sum(1 for t in trials if t["ends_clean"]),
                "trials": trials,
            }

    out = ROOT / "_bench" / (args.out or f"p4_compare_{args.prompt_set}.json")
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
