"""Voice-note API (Phase 4).

Audio is uploaded as a raw request body (no multipart dependency), streamed to
a generated local filename, and never stored in SQLite. Transcription runs as
an in-process background task; failures are isolated from chat, memories, and
existing conversation behavior.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ..config import MAX_VOICE_NOTE_TITLE_CHARS
from ..db.connection import db_connection
from ..db.utils import new_id, utc_now
from ..models.schemas import (
    VoiceNoteOut,
    VoiceNoteSummaryOut,
    VoiceNoteToChatOut,
    VoiceNoteTranscribeIn,
    VoiceNoteUpdateIn,
)
from ..services import voice_notes as store
from ..services.chat_service import insert_message
from ..services.transcription import (
    ERROR_ENGINE_FAILED,
    FfmpegWhisperEngine,
    TranscriptionFailure,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_ID_RE = re.compile(r"^[0-9a-f]{32}$")

_ERROR_MESSAGES = {
    "ffmpeg_missing": "The local ffmpeg binary was not found.",
    "model_missing": "The local transcription model is missing or could not be loaded.",
    "model_path_not_relocatable": "The transcription model path is not usable from the voice-notes directory.",
    "timeout": "Transcription timed out.",
    "engine_failed": "The transcription engine failed.",
    "empty_transcript": "The recording produced no speech.",
    "invalid_output": "The transcription engine returned unreadable output.",
}


def _validate_id(note_id: str) -> str:
    if not _ID_RE.fullmatch(note_id):
        raise HTTPException(status_code=422, detail="Invalid voice note id.")
    return note_id


def _storage_failure(exc: Exception, message: str) -> HTTPException:
    logger.warning("Voice note operation failed: %s", exc.__class__.__name__)
    return HTTPException(status_code=500, detail=message)


async def run_transcription(settings, engine, note_id: str) -> None:
    """Background task: transcribe one note and persist a safe outcome."""
    try:
        with db_connection(settings.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM voice_notes WHERE id = ?", (note_id,)
            ).fetchone()
            if row is None:
                return
            try:
                audio = store.audio_path(settings, row["audio_filename"])
            except store.VoiceNoteStorageError:
                store.fail_transcription(conn, note_id, "audio_missing")
                return
        if not audio.is_file():
            with db_connection(settings.db_path) as conn:
                store.fail_transcription(conn, note_id, "audio_missing")
            return
        outcome = await engine.transcribe(settings, audio)
        with db_connection(settings.db_path) as conn:
            store.finish_transcription(
                conn, note_id, transcript=outcome.text, language=outcome.language
            )
        logger.info("Voice note transcribed (id=%s).", note_id)
    except TranscriptionFailure as exc:
        with db_connection(settings.db_path) as conn:
            store.fail_transcription(conn, note_id, exc.code)
        logger.info("Voice note transcription failed (id=%s, code=%s).", note_id, exc.code)
    except Exception as exc:  # never let a background task break the app
        with db_connection(settings.db_path) as conn:
            store.fail_transcription(conn, note_id, ERROR_ENGINE_FAILED)
        logger.warning(
            "Voice note transcription failed unexpectedly (id=%s, error=%s).",
            note_id,
            exc.__class__.__name__,
        )


@router.get("/api/voice-notes", response_model=list[VoiceNoteSummaryOut])
async def list_voice_notes(
    request: Request,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[VoiceNoteSummaryOut]:
    settings = request.app.state.settings
    try:
        with db_connection(settings.db_path) as conn:
            rows, _ = store.list_voice_notes(conn, limit, offset)
            return [store.to_summary(settings, row) for row in rows]
    except sqlite3.Error as exc:
        raise _storage_failure(exc, "Could not list voice notes.") from exc


@router.post("/api/voice-notes", response_model=VoiceNoteOut, status_code=201)
async def upload_voice_note(
    request: Request,
    title: str | None = Query(default=None, max_length=MAX_VOICE_NOTE_TITLE_CHARS),
) -> VoiceNoteOut:
    """Store one recording/import.

    The client sends the audio bytes as the body and a supported audio
    `Content-Type`; the server chooses the extension and the generated filename.
    """
    settings = request.app.state.settings
    content_type = request.headers.get("content-type", "")
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            if int(declared_length) > settings.voice_note_max_bytes:
                raise HTTPException(status_code=413, detail="Audio file is too large.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.") from exc

    try:
        extension = store.extension_for(content_type)
    except store.VoiceNoteUnsupportedType as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    note_id = new_id()
    filename = f"{note_id}{extension}"
    try:
        audio_bytes = await store.store_audio(
            settings,
            filename,
            request.stream(),
            settings.voice_note_max_bytes,
        )
    except store.VoiceNoteTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except store.VoiceNoteEmpty as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except store.VoiceNoteStorageError as exc:
        raise _storage_failure(exc, "Could not store the audio file.") from exc
    except Exception as exc:  # client disconnect or unexpected I/O
        logger.warning("Audio upload failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=400, detail="Audio upload was interrupted.") from exc

    clean_title = (title or "").strip()[:MAX_VOICE_NOTE_TITLE_CHARS] or "Voice note"
    try:
        path = store.audio_path(settings, filename)
        decodable, duration = store.probe_media(path)
        if decodable is False:
            store.remove_audio_file(settings, filename)
            raise HTTPException(
                status_code=415, detail="The uploaded file is not decodable audio."
            )
        with db_connection(settings.db_path) as conn:
            row = store.create_voice_note(
                conn,
                note_id=note_id,
                title=clean_title,
                audio_filename=filename,
                audio_content_type=content_type,
                audio_bytes=audio_bytes,
                audio_duration_seconds=duration,
            )
            return store.to_detail(settings, row)
    except HTTPException:
        raise
    except Exception as exc:
        store.remove_audio_file(settings, filename)
        raise _storage_failure(exc, "Could not save the voice note.") from exc


@router.get("/api/voice-notes/{note_id}", response_model=VoiceNoteOut)
async def get_voice_note(note_id: str, request: Request) -> VoiceNoteOut:
    note_id = _validate_id(note_id)
    settings = request.app.state.settings
    try:
        with db_connection(settings.db_path) as conn:
            row = store.require_voice_note(conn, note_id)
            return store.to_detail(settings, row)
    except store.VoiceNoteNotFound as exc:
        raise HTTPException(status_code=404, detail="Voice note not found.") from exc
    except sqlite3.Error as exc:
        raise _storage_failure(exc, "Could not load the voice note.") from exc


@router.get("/api/voice-notes/{note_id}/audio")
async def get_voice_note_audio(note_id: str, request: Request) -> FileResponse:
    note_id = _validate_id(note_id)
    settings = request.app.state.settings
    with db_connection(settings.db_path) as conn:
        try:
            row = store.require_voice_note(conn, note_id)
        except store.VoiceNoteNotFound as exc:
            raise HTTPException(status_code=404, detail="Voice note not found.") from exc
    if row["audio_deleted_at"] is not None:
        raise HTTPException(
            status_code=410, detail="The audio was deleted; its transcript is kept."
        )
    try:
        path = store.audio_path(settings, row["audio_filename"])
    except store.VoiceNoteStorageError as exc:
        raise HTTPException(status_code=404, detail="Audio file is unavailable.") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio file is missing.")
    return FileResponse(
        path,
        media_type=row["audio_content_type"],
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "inline",
        },
    )


@router.patch("/api/voice-notes/{note_id}", response_model=VoiceNoteOut)
async def update_voice_note(
    note_id: str, payload: VoiceNoteUpdateIn, request: Request
) -> VoiceNoteOut:
    note_id = _validate_id(note_id)
    settings = request.app.state.settings
    try:
        with db_connection(settings.db_path) as conn:
            store.update_voice_note(
                conn,
                note_id,
                title=payload.title,
                transcript=payload.transcript,
            )
            return store.to_detail(settings, store.require_voice_note(conn, note_id))
    except store.VoiceNoteNotFound as exc:
        raise HTTPException(status_code=404, detail="Voice note not found.") from exc
    except store.VoiceNoteConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except sqlite3.Error as exc:
        raise _storage_failure(exc, "Could not update the voice note.") from exc


@router.post(
    "/api/voice-notes/{note_id}/transcribe",
    response_model=VoiceNoteOut,
    status_code=202,
)
async def transcribe_voice_note(
    note_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: VoiceNoteTranscribeIn | None = None,
) -> VoiceNoteOut:
    note_id = _validate_id(note_id)
    settings = request.app.state.settings
    engine = getattr(request.app.state, "transcription", FfmpegWhisperEngine())
    unavailable = engine.availability(settings)
    if unavailable is not None:
        raise HTTPException(
            status_code=503,
            detail=_ERROR_MESSAGES.get(
                unavailable, "Local transcription is unavailable."
            ),
        )
    try:
        with db_connection(settings.db_path) as conn:
            store.prepare_transcription(
                conn, settings, note_id, payload.force if payload else False
            )
            row = store.require_voice_note(conn, note_id)
            result = store.to_detail(settings, row)
    except store.VoiceNoteNotFound as exc:
        raise HTTPException(status_code=404, detail="Voice note not found.") from exc
    except store.VoiceNoteCorrectionProtected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except store.VoiceNoteConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except store.VoiceNoteAudioMissing as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except sqlite3.Error as exc:
        raise _storage_failure(exc, "Could not start transcription.") from exc
    background_tasks.add_task(run_transcription, settings, engine, note_id)
    return result


@router.post(
    "/api/voice-notes/{note_id}/send-to-chat",
    response_model=VoiceNoteToChatOut,
    status_code=201,
)
async def send_voice_note_to_chat(
    note_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> VoiceNoteToChatOut:
    """Explicitly use a transcript as a memory source.

    Creates one new conversation with the corrected transcript as a user
    message and runs the existing extraction task on it. No assistant reply is
    generated, and nothing is extracted automatically without this action.
    """
    note_id = _validate_id(note_id)
    settings = request.app.state.settings
    try:
        with db_connection(settings.db_path) as conn:
            note = store.require_voice_note(conn, note_id)
            if note["transcript_status"] == "processing":
                raise HTTPException(
                    status_code=409, detail="Wait for transcription to finish first."
                )
            transcript = (note["transcript"] or "").strip()
            if not transcript:
                raise HTTPException(
                    status_code=409, detail="This voice note has no transcript yet."
                )
            conversation_id = new_id()
            now = utc_now()
            title = f"Voice note: {note['title']}"[:200]
            conn.execute(
                "INSERT INTO conversations (id, title, status, created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
                (conversation_id, title, now, now),
            )
            message = insert_message(conn, conversation_id, "user", transcript)
    except store.VoiceNoteNotFound as exc:
        raise HTTPException(status_code=404, detail="Voice note not found.") from exc
    except sqlite3.Error as exc:
        raise _storage_failure(exc, "Could not use the transcript.") from exc

    request.app.state.extraction_scheduler.schedule(
        background_tasks, conversation_id, message.seq
    )
    logger.info("Voice-note transcript queued for extraction (id=%s).", note_id)
    return VoiceNoteToChatOut(conversation_id=conversation_id, message_id=message.id)


@router.delete("/api/voice-notes/{note_id}/audio", status_code=204)
async def delete_voice_note_audio(note_id: str, request: Request) -> None:
    note_id = _validate_id(note_id)
    settings = request.app.state.settings
    try:
        with db_connection(settings.db_path) as conn:
            store.delete_audio(conn, settings, note_id)
    except store.VoiceNoteNotFound as exc:
        raise HTTPException(status_code=404, detail="Voice note not found.") from exc
    except store.VoiceNoteStorageError as exc:
        raise _storage_failure(exc, "Could not delete the audio file.") from exc
    except sqlite3.Error as exc:
        raise _storage_failure(exc, "Could not delete the audio file.") from exc


@router.delete("/api/voice-notes/{note_id}", status_code=204)
async def delete_voice_note(note_id: str, request: Request) -> None:
    note_id = _validate_id(note_id)
    settings = request.app.state.settings
    try:
        with db_connection(settings.db_path) as conn:
            store.delete_voice_note(conn, settings, note_id)
    except store.VoiceNoteNotFound as exc:
        raise HTTPException(status_code=404, detail="Voice note not found.") from exc
    except store.VoiceNoteStorageError as exc:
        raise _storage_failure(exc, "Could not delete the voice note.") from exc
    except sqlite3.Error as exc:
        raise _storage_failure(exc, "Could not delete the voice note.") from exc
