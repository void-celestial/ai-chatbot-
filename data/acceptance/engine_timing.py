"""Measure the transcription engine's own processing time (no HTTP, no polling)."""

import asyncio
import json
import shutil
import time
from pathlib import Path

from backend.app.config import load_settings
from backend.app.services.transcription import FfmpegWhisperEngine

ROOT = Path("data/acceptance")


def main() -> None:
    settings = load_settings()
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    results = []
    for item in manifest:
        source = ROOT / item["file"]
        target = settings.voice_notes_dir / f"timing-{item['name']}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        started = time.perf_counter()
        outcome = asyncio.run(FfmpegWhisperEngine().transcribe(settings, target))
        elapsed = time.perf_counter() - started
        target.unlink(missing_ok=True)
        results.append(
            {
                "lang": item["lang"],
                "name": item["name"],
                "duration": item["duration"],
                "processing_seconds": elapsed,
                "real_time_factor": elapsed / item["duration"],
                "text": outcome.text,
                "language": outcome.language,
            }
        )
        print(
            item["lang"],
            item["name"],
            f"audio={item['duration']:.2f}s",
            f"processing={elapsed:.2f}s",
            f"rtf={elapsed / item['duration']:.2f}",
            flush=True,
        )
    (ROOT / "engine_results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
