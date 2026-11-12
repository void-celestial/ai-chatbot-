"""Phase 4 end-to-end acceptance run against the real FastAPI server.

Uploads real FLEURS reference audio over HTTP, starts local transcription, polls
the note until the background job finishes, and records timings. No transcriber
fakes are used.
"""

import json
import time
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8011"
ROOT = Path("data/acceptance")


def main() -> None:
    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    results = []
    with httpx.Client(timeout=120.0) as client:
        for item in manifest:
            payload = (ROOT / item["file"]).read_bytes()
            started = time.perf_counter()
            upload = client.post(
                f"{BASE}/api/voice-notes",
                params={"title": item["name"]},
                content=payload,
                headers={"Content-Type": "audio/wav"},
            )
            upload.raise_for_status()
            upload_seconds = time.perf_counter() - started
            note = upload.json()
            audio = client.get(f"{BASE}/api/voice-notes/{note['id']}/audio")
            playback_ok = audio.status_code == 200 and audio.content == payload

            started = time.perf_counter()
            transcribe = client.post(f"{BASE}/api/voice-notes/{note['id']}/transcribe")
            dispatch_ok = transcribe.status_code == 202
            deadline = started + 420
            detail = {}
            while time.perf_counter() < deadline:
                detail = client.get(f"{BASE}/api/voice-notes/{note['id']}").json()
                if detail["transcript_status"] in ("completed", "failed"):
                    break
                time.sleep(0.2)
            end_to_end = time.perf_counter() - started

            results.append(
                {
                    "lang": item["lang"],
                    "name": item["name"],
                    "duration": item["duration"],
                    "reference": item["reference"],
                    "note_id": note["id"],
                    "status": detail.get("transcript_status"),
                    "error": detail.get("transcript_error"),
                    "language_field": detail.get("transcript_language"),
                    "transcript": detail.get("transcript") or "",
                    "probed_duration": detail.get("audio_duration_seconds"),
                    "upload_seconds": upload_seconds,
                    "dispatch_status": transcribe.status_code if dispatch_ok else transcribe.status_code,
                    "end_to_end_seconds": end_to_end,
                    "playback_bytes_match": playback_ok,
                }
            )
            print(
                item["lang"],
                item["name"],
                detail.get("transcript_status"),
                f"e2e={end_to_end:.2f}s",
                flush=True,
            )

    (ROOT / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("saved", len(results), "results")


if __name__ == "__main__":
    main()
