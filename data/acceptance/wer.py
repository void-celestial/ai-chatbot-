"""Word error rate for the Phase 4 acceptance run (no external dependency)."""

import json
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path("data/acceptance")


def normalize(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text).casefold()
    # Drop punctuation and symbols (danda, quotes, commas, hyphens, ...).
    text = "".join(
        " " if unicodedata.category(ch).startswith(("P", "S")) else ch for ch in text
    )
    text = re.sub(r"\s+", " ", text).strip()
    return text.split()


def edit_ops(reference: list[str], hypothesis: list[str]) -> dict[str, int]:
    rows, cols = len(reference) + 1, len(hypothesis) + 1
    cost = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        cost[i][0] = i
    for j in range(cols):
        cost[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            substitute = cost[i - 1][j - 1] + (reference[i - 1] != hypothesis[j - 1])
            cost[i][j] = min(substitute, cost[i - 1][j] + 1, cost[i][j - 1] + 1)

    substitutions = deletions = insertions = 0
    i, j = len(reference), len(hypothesis)
    while i > 0 or j > 0:
        if i > 0 and j > 0 and cost[i][j] == cost[i - 1][j - 1] + (
            reference[i - 1] != hypothesis[j - 1]
        ):
            if reference[i - 1] != hypothesis[j - 1]:
                substitutions += 1
            i, j = i - 1, j - 1
        elif i > 0 and cost[i][j] == cost[i - 1][j] + 1:
            deletions += 1
            i -= 1
        else:
            insertions += 1
            j -= 1
    return {
        "substitutions": substitutions,
        "deletions": deletions,
        "insertions": insertions,
        "errors": substitutions + deletions + insertions,
    }


def main() -> None:
    results = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
    timing = {
        row["name"]: row
        for row in json.loads((ROOT / "engine_results.json").read_text(encoding="utf-8"))
    }
    totals = {"errors": 0, "words": 0, "char_errors": 0, "chars": 0}
    by_lang: dict[str, dict[str, int]] = {}
    print(
        f"{'lang':5} {'clip':26} {'dur':>6} {'proc':>7} {'rtf':>5} "
        f"{'e2e':>7} {'S':>3} {'D':>3} {'I':>3} {'WER':>7}"
    )
    for row in results:
        ref = normalize(row["reference"])
        hyp = normalize(row["transcript"])
        ops = edit_ops(ref, hyp)
        wer = ops["errors"] / max(1, len(ref))
        ref_chars = list("".join(ref))
        hyp_chars = list("".join(hyp))
        char_ops = edit_ops(ref_chars, hyp_chars)
        engine = timing[row["name"]]
        totals["errors"] += ops["errors"]
        totals["words"] += len(ref)
        totals["char_errors"] += char_ops["errors"]
        totals["chars"] += len(ref_chars)
        bucket = by_lang.setdefault(
            row["lang"], {"errors": 0, "words": 0, "char_errors": 0, "chars": 0}
        )
        bucket["errors"] += ops["errors"]
        bucket["words"] += len(ref)
        bucket["char_errors"] += char_ops["errors"]
        bucket["chars"] += len(ref_chars)
        print(
            f"{row['lang']:5} {row['name']:26} {row['duration']:6.2f} "
            f"{engine['processing_seconds']:7.2f} {engine['real_time_factor']:5.2f} "
            f"{row['end_to_end_seconds']:7.2f} {ops['substitutions']:3d} "
            f"{ops['deletions']:3d} {ops['insertions']:3d} {wer * 100:6.1f}% "
            f"CER {char_ops['errors'] / max(1, len(ref_chars)) * 100:5.1f}%"
        )
    print()
    for lang, bucket in by_lang.items():
        print(
            f"{lang} corpus WER: {bucket['errors'] / bucket['words'] * 100:.1f}% "
            f"({bucket['errors']} errors / {bucket['words']} reference words) | "
            f"CER {bucket['char_errors'] / bucket['chars'] * 100:.1f}%"
        )
    print(
        f"all-clip corpus WER: {totals['errors'] / totals['words'] * 100:.1f}% "
        f"({totals['errors']} / {totals['words']}) | "
        f"CER {totals['char_errors'] / totals['chars'] * 100:.1f}%"
    )
    durations = [r["duration"] for r in results]
    proc = sum(timing[r["name"]]["processing_seconds"] for r in results)
    e2e = sum(r["end_to_end_seconds"] for r in results)
    print(f"audio total: {sum(durations):.2f}s | engine total: {proc:.2f}s "
          f"(RTF {proc / sum(durations):.2f}) | HTTP+engine total: {e2e:.2f}s")


if __name__ == "__main__":
    sys.exit(main())
