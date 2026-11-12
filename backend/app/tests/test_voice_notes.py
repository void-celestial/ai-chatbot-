"""Phase 4: voice-note storage, playback, transcription, and correction.

The transcription engine is a deterministic fake; no model or ffmpeg run is
required. Uploaded audio is a real, decodable WAV so the optional ffprobe
format check can be exercised when the tool is installed.
"""

import io
import shutil
import sqlite3
import wave
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db.connection import db_connection
from backend.app.main import create_app
from backend.app.services import voice_notes as store
from backend.app.services.voice_notes import recover_interrupted_transcriptions

from .conftest import FakeTranscriptionEngine, TEST_MODEL_TAG


def wav_bytes(duration: float = 0.1, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * duration))
    return buffer.getvalue()


def upload(client: TestClient, *, title: str | None = None, content: bytes | None = None):
    params = {"title": title} if title is not None else None
    return client.post(
        "/api/voice-notes",
        params=params,
        content=content if content is not None else wav_bytes(),
        headers={"content-type": "audio/wav"},
    )


def note_files(settings: Settings) -> list[Path]:
    directory = settings.voice_notes_dir
    return sorted(directory.iterdir()) if directory.exists() else []


# ------------------------------------------------------------------ upload ---


def test_voice_note_list_starts_empty(client):
    response = client.get("/api/voice-notes")
    assert response.status_code == 200
    assert response.json() == []


def test_upload_stores_generated_file_and_metadata(client, settings):
    response = upload(client, title="  Morning idea  ")

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["title"] == "Morning idea"
    assert body["transcript_status"] == "pending"
    assert body["audio_available"] is True
    assert body["audio_deleted"] is False
    assert body["audio_content_type"] == "audio/wav"
    assert body["audio_bytes"] == len(wav_bytes())
    assert body["transcript"] is None
    assert len(body["id"]) == 32 and body["id"].isalnum()
    assert str(settings.voice_notes_dir) not in str(body)  # no filesystem path leaked
    files = note_files(settings)
    assert [f.name for f in files] == [f"{body['id']}.wav"]
    assert files[0].parent == settings.voice_notes_dir


def test_upload_defaults_title_and_accepts_content_type_parameters(client):
    response = client.post(
        "/api/voice-notes",
        content=wav_bytes(),
        headers={"content-type": "audio/wav; codecs=1"},
    )
    assert response.status_code == 201
    assert response.json()["title"] == "Voice note"


def test_upload_rejects_unsupported_content_type_without_writing_a_file(client, settings):
    response = client.post(
        "/api/voice-notes", content=b"hello", headers={"content-type": "text/plain"}
    )
    assert response.status_code == 415
    assert note_files(settings) == []


def test_upload_rejects_empty_body(client, settings):
    response = client.post(
        "/api/voice-notes", content=b"", headers={"content-type": "audio/wav"}
    )
    assert response.status_code == 400
    assert note_files(settings) == []


def test_upload_enforces_size_limit_and_removes_partial_file(tmp_path):
    settings = Settings(
        db_path=tmp_path / "limited.db",
        model_tag=TEST_MODEL_TAG,
        voice_notes_dir=tmp_path / "audio",
        whisper_model_path=tmp_path / "models" / "x.bin",
        voice_note_max_bytes=32,
    )
    app = create_app(settings=settings, transcription=FakeTranscriptionEngine())
    client = TestClient(app)

    response = client.post(
        "/api/voice-notes", content=wav_bytes(), headers={"content-type": "audio/wav"}
    )

    assert response.status_code == 413
    assert note_files(settings) == []


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe not installed")
def test_upload_rejects_undecodable_audio_when_ffprobe_is_available(client, settings):
    response = client.post(
        "/api/voice-notes", content=b"not audio", headers={"content-type": "audio/wav"}
    )
    assert response.status_code == 415
    assert note_files(settings) == []


def test_upload_does_not_create_messages_or_memories(client, settings):
    note = upload(client).json()
    with closing(sqlite3.connect(settings.db_path)) as raw:
        assert raw.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 0
        assert raw.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    assert client.get(f"/api/voice-notes/{note['id']}").status_code == 200


# --------------------------------------------------------- IDs and retrieval ---


def test_invalid_and_unknown_ids_are_rejected(client):
    assert client.get("/api/voice-notes/not-an-id").status_code == 422
    assert client.get("/api/voice-notes/" + "0" * 32).status_code == 404
    assert client.delete("/api/voice-notes/" + "0" * 32).status_code == 404
    assert client.post("/api/voice-notes/" + "0" * 32 + "/transcribe").status_code == 404


def test_audio_can_be_streamed_and_plays_inline(client):
    note = upload(client).json()

    response = client.get(f"/api/voice-notes/{note['id']}/audio")

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.headers["content-disposition"] == "inline"
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"RIFF")


def test_missing_audio_file_is_reported_without_losing_the_note(client, settings):
    note = upload(client).json()
    next(settings.voice_notes_dir.iterdir()).unlink()

    audio = client.get(f"/api/voice-notes/{note['id']}/audio")
    detail = client.get(f"/api/voice-notes/{note['id']}")

    assert audio.status_code == 404
    assert detail.status_code == 200
    assert detail.json()["audio_available"] is False
    assert detail.json()["audio_deleted"] is False


def test_listing_is_newest_first_and_paginates(client):
    ids = [upload(client, title=f"Note {index}").json()["id"] for index in range(3)]

    first = client.get("/api/voice-notes", params={"limit": 2, "offset": 0}).json()
    second = client.get("/api/voice-notes", params={"limit": 2, "offset": 2}).json()

    assert [item["id"] for item in first] == list(reversed(ids))[:2]
    assert [item["id"] for item in second] == [ids[0]]
    assert first[0]["transcript_status"] == "pending"


# ---------------------------------------------------------------- transcribe ---


def test_transcription_success_persists_transcript_and_language(
    client, fake_transcription
):
    note = upload(client).json()

    response = client.post(f"/api/voice-notes/{note['id']}/transcribe")

    assert response.status_code == 202
    assert response.json()["transcript_status"] == "processing"
    detail = client.get(f"/api/voice-notes/{note['id']}").json()
    assert detail["transcript_status"] == "completed"
    assert detail["transcript"] == fake_transcription.text
    assert detail["machine_transcript"] == fake_transcription.text
    assert detail["transcript_source"] == "machine"
    assert detail["transcript_language"] == "en"
    assert detail["transcript_error"] is None
    assert len(fake_transcription.calls) == 1
    assert fake_transcription.calls[0].name == f"{note['id']}.wav"


def test_transcription_failure_is_reported_and_audio_is_kept(
    client, fake_transcription
):
    note = upload(client).json()
    fake_transcription.behavior = "fail"

    assert client.post(f"/api/voice-notes/{note['id']}/transcribe").status_code == 202

    detail = client.get(f"/api/voice-notes/{note['id']}").json()
    assert detail["transcript_status"] == "failed"
    assert detail["transcript_error"] == "engine_failed"
    assert detail["audio_available"] is True
    assert detail["transcript"] is None


def test_transcription_timeout_is_reported(client, fake_transcription):
    note = upload(client).json()
    fake_transcription.behavior = "timeout"

    assert client.post(f"/api/voice-notes/{note['id']}/transcribe").status_code == 202

    assert (
        client.get(f"/api/voice-notes/{note['id']}").json()["transcript_error"]
        == "timeout"
    )


def test_unavailable_engine_returns_actionable_503_without_processing(
    client, fake_transcription
):
    note = upload(client).json()
    fake_transcription.availability_reason = "model_missing"

    response = client.post(f"/api/voice-notes/{note['id']}/transcribe")

    assert response.status_code == 503
    assert "model" in response.json()["detail"].lower()
    assert fake_transcription.calls == []
    assert (
        client.get(f"/api/voice-notes/{note['id']}").json()["transcript_status"]
        == "pending"
    )


def test_duplicate_transcription_is_refused_by_the_claim(
    client, settings, fake_transcription
):
    note = upload(client).json()
    with db_connection(settings.db_path) as conn:
        store.prepare_transcription(conn, settings, note["id"], False)
        with pytest.raises(store.VoiceNoteConflict):
            store.prepare_transcription(conn, settings, note["id"], False)


def test_transcription_requires_existing_audio(client, settings, fake_transcription):
    note = upload(client).json()
    next(settings.voice_notes_dir.iterdir()).unlink()

    response = client.post(f"/api/voice-notes/{note['id']}/transcribe")

    assert response.status_code == 409
    assert "missing" in response.json()["detail"].lower()
    assert fake_transcription.calls == []


def test_deleted_audio_cannot_be_transcribed(client, fake_transcription):
    note = upload(client).json()
    assert client.delete(f"/api/voice-notes/{note['id']}/audio").status_code == 204

    response = client.post(f"/api/voice-notes/{note['id']}/transcribe")

    assert response.status_code == 409
    assert "deleted" in response.json()["detail"].lower()
    assert fake_transcription.calls == []


# --------------------------------------------------------------- corrections ---


def test_user_correction_is_stored_separately_from_machine_text(client):
    note = upload(client).json()
    client.post(f"/api/voice-notes/{note['id']}/transcribe")
    original = client.get(f"/api/voice-notes/{note['id']}").json()["machine_transcript"]

    response = client.patch(
        f"/api/voice-notes/{note['id']}", json={"transcript": "  Corrected wording.  "}
    )

    assert response.status_code == 200
    detail = response.json()
    assert detail["transcript"] == "Corrected wording."
    assert detail["transcript_source"] == "user"
    assert detail["machine_transcript"] == original
    assert detail["transcript_status"] == "completed"


def test_blank_or_empty_correction_is_rejected(client):
    note = upload(client).json()

    assert (
        client.patch(f"/api/voice-notes/{note['id']}", json={"transcript": "   "}).status_code
        == 422
    )
    assert client.patch(f"/api/voice-notes/{note['id']}", json={}).status_code == 422
    assert (
        client.patch(
            f"/api/voice-notes/{note['id']}", json={"transcript": "ok", "status": "completed"}
        ).status_code
        == 422
    )
    assert client.get(f"/api/voice-notes/{note['id']}").json()["transcript"] is None


def test_retranscription_refuses_to_overwrite_a_correction_without_force(
    client, fake_transcription
):
    note = upload(client).json()
    client.post(f"/api/voice-notes/{note['id']}/transcribe")
    client.patch(f"/api/voice-notes/{note['id']}", json={"transcript": "My correction."})
    fake_transcription.calls.clear()

    response = client.post(f"/api/voice-notes/{note['id']}/transcribe")

    assert response.status_code == 409
    assert "corrected" in response.json()["detail"].lower()
    assert fake_transcription.calls == []
    detail = client.get(f"/api/voice-notes/{note['id']}").json()
    assert detail["transcript"] == "My correction."
    assert detail["transcript_source"] == "user"


def test_forced_retranscription_replaces_the_correction(client, fake_transcription):
    note = upload(client).json()
    client.post(f"/api/voice-notes/{note['id']}/transcribe")
    client.patch(f"/api/voice-notes/{note['id']}", json={"transcript": "My correction."})
    fake_transcription.text = "Fresh machine text."

    response = client.post(
        f"/api/voice-notes/{note['id']}/transcribe", json={"force": True}
    )

    assert response.status_code == 202
    detail = client.get(f"/api/voice-notes/{note['id']}").json()
    assert detail["transcript"] == "Fresh machine text."
    assert detail["transcript_source"] == "machine"


def test_transcript_edit_is_refused_while_transcription_is_running(client, settings):
    note = upload(client).json()
    with db_connection(settings.db_path) as conn:
        store.prepare_transcription(conn, settings, note["id"], False)

    response = client.patch(
        f"/api/voice-notes/{note['id']}", json={"transcript": "too early"}
    )

    assert response.status_code == 409
    detail = client.get(f"/api/voice-notes/{note['id']}").json()
    assert detail["transcript"] is None
    assert detail["transcript_status"] == "processing"


def test_late_transcription_does_not_overwrite_changed_state(client, settings):
    note = upload(client).json()
    with db_connection(settings.db_path) as conn:
        store.prepare_transcription(conn, settings, note["id"], False)
        # Simulate a manual fallback or a deletion racing the background job.
        conn.execute(
            """UPDATE voice_notes
               SET transcript = 'User wrote this.', transcript_source = 'user',
                   transcript_status = 'completed'
               WHERE id = ?""",
            (note["id"],),
        )

    with db_connection(settings.db_path) as conn:
        store.finish_transcription(
            conn, note["id"], transcript="Machine text.", language="en"
        )
        store.fail_transcription(conn, note["id"], "engine_failed")

    detail = client.get(f"/api/voice-notes/{note['id']}").json()
    assert detail["transcript"] == "User wrote this."
    assert detail["transcript_source"] == "user"
    assert detail["transcript_status"] == "completed"


def test_failed_forced_retranscription_keeps_the_user_correction(
    client, fake_transcription
):
    note = upload(client).json()
    client.post(f"/api/voice-notes/{note['id']}/transcribe")
    client.patch(f"/api/voice-notes/{note['id']}", json={"transcript": "My correction."})
    fake_transcription.behavior = "fail"

    client.post(f"/api/voice-notes/{note['id']}/transcribe", json={"force": True})

    detail = client.get(f"/api/voice-notes/{note['id']}").json()
    assert detail["transcript"] == "My correction."
    assert detail["transcript_source"] == "user"
    assert detail["transcript_status"] == "failed"


def test_correction_survives_a_restart(tmp_path, fake_transcription):
    settings = Settings(
        db_path=tmp_path / "restart.db",
        model_tag=TEST_MODEL_TAG,
        voice_notes_dir=tmp_path / "audio",
        whisper_model_path=tmp_path / "models" / "x.bin",
    )
    first = TestClient(create_app(settings=settings, transcription=fake_transcription))
    note = upload(first).json()
    first.post(f"/api/voice-notes/{note['id']}/transcribe")
    first.patch(f"/api/voice-notes/{note['id']}", json={"transcript": "Persisted edit."})

    second = TestClient(create_app(settings=settings, transcription=fake_transcription))
    detail = second.get(f"/api/voice-notes/{note['id']}").json()

    assert detail["transcript"] == "Persisted edit."
    assert detail["transcript_source"] == "user"


# ------------------------------------------- explicit transcript to memory ---


def test_corrected_transcript_can_be_used_as_an_explicit_memory_source(
    client, fake_ollama
):
    note = upload(client, title="Morning idea").json()
    client.post(f"/api/voice-notes/{note['id']}/transcribe")
    client.patch(
        f"/api/voice-notes/{note['id']}",
        json={"transcript": "My sister Mara lives in Bergen."},
    )

    response = client.post(f"/api/voice-notes/{note['id']}/send-to-chat")

    assert response.status_code == 201, response.text
    body = response.json()
    conversation = client.get(f"/api/conversations/{body['conversation_id']}").json()
    messages = client.get(
        f"/api/conversations/{body['conversation_id']}/messages"
    ).json()
    assert conversation["title"] == "Voice note: Morning idea"
    assert [m["role"] for m in messages] == ["user"]
    assert messages[0]["content"] == "My sister Mara lives in Bergen."
    assert messages[0]["id"] == body["message_id"]
    # The normal extraction task ran on the corrected text, without inventing
    # an assistant message or changing the transcript.
    assert fake_ollama.chat_json_calls
    assert "My sister Mara lives in Bergen." in fake_ollama.chat_json_calls[-1][
        "messages"
    ][1]["content"]


def test_send_to_chat_requires_a_finished_transcript(client):
    note = upload(client).json()
    assert (
        client.post(f"/api/voice-notes/{note['id']}/send-to-chat").status_code == 409
    )

    with db_connection(client.app.state.settings.db_path) as conn:
        conn.execute(
            "UPDATE voice_notes SET transcript_status = 'processing', transcript = 'x' WHERE id = ?",
            (note["id"],),
        )
    assert (
        client.post(f"/api/voice-notes/{note['id']}/send-to-chat").status_code == 409
    )
    assert client.get("/api/conversations").json() == []


def test_send_to_chat_does_not_duplicate_without_a_user_action(client, fake_ollama):
    note = upload(client).json()
    client.post(f"/api/voice-notes/{note['id']}/transcribe")
    calls_after_upload = len(fake_ollama.chat_json_calls)

    assert len(client.get("/api/conversations").json()) == 0
    assert len(fake_ollama.chat_json_calls) == calls_after_upload


# ------------------------------------------------------------------ deletion ---


def test_deleting_audio_keeps_transcript_and_is_idempotent(client, settings):
    note = upload(client).json()
    client.post(f"/api/voice-notes/{note['id']}/transcribe")
    transcript = client.get(f"/api/voice-notes/{note['id']}").json()["transcript"]

    first = client.delete(f"/api/voice-notes/{note['id']}/audio")
    second = client.delete(f"/api/voice-notes/{note['id']}/audio")

    assert first.status_code == 204 and second.status_code == 204
    assert note_files(settings) == []
    detail = client.get(f"/api/voice-notes/{note['id']}").json()
    assert detail["audio_available"] is False
    assert detail["audio_deleted"] is True
    assert detail["transcript"] == transcript
    assert detail["machine_transcript"] == transcript
    audio = client.get(f"/api/voice-notes/{note['id']}/audio")
    assert audio.status_code == 410
    assert "transcript" in audio.json()["detail"].lower()


def test_deleting_note_removes_row_and_file(client, settings):
    note = upload(client).json()

    assert client.delete(f"/api/voice-notes/{note['id']}").status_code == 204

    assert note_files(settings) == []
    assert client.get(f"/api/voice-notes/{note['id']}").status_code == 404
    assert client.delete(f"/api/voice-notes/{note['id']}").status_code == 404


def test_delete_with_missing_file_still_removes_the_row(client, settings):
    note = upload(client).json()
    next(settings.voice_notes_dir.iterdir()).unlink()

    assert client.delete(f"/api/voice-notes/{note['id']}").status_code == 204
    assert client.get(f"/api/voice-notes/{note['id']}").status_code == 404


# ------------------------------------------------------- recovery and paths ---


def test_interrupted_transcription_is_reported_after_restart(client, settings):
    note = upload(client).json()
    with db_connection(settings.db_path) as conn:
        conn.execute(
            "UPDATE voice_notes SET transcript_status = 'processing' WHERE id = ?",
            (note["id"],),
        )

    assert recover_interrupted_transcriptions(settings.db_path) == 1

    detail = client.get(f"/api/voice-notes/{note['id']}").json()
    assert detail["transcript_status"] == "failed"
    assert detail["transcript_error"] == "interrupted_by_restart"


def test_audio_path_rejects_traversal_and_unsafe_names(settings):
    for bad in ("../secret.wav", "..%2fsecret.wav", "abc.wav", "a" * 32 + ".wav/.."):
        with pytest.raises(store.VoiceNoteStorageError):
            store.audio_path(settings, bad)


def test_title_can_be_updated_and_validated(client):
    note = upload(client).json()
    assert (
        client.patch(f"/api/voice-notes/{note['id']}", json={"title": " Renamed "}).json()[
            "title"
        ]
        == "Renamed"
    )
    assert (
        client.patch(f"/api/voice-notes/{note['id']}", json={"title": "   "}).status_code
        == 422
    )
