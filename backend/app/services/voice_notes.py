"""Local storage and lifecycle for human-recorded voice notes (Phase 4).

Audio bytes live at `<voice_notes_dir>/<id>.<ext>`; SQLite stores only metadata
and transcript text. File names are generated from the note id and an
allowlisted content type, so user-supplied names never become paths.

Deletion is explicit: deleting the audio keeps the note/transcript and marks
`audio_deleted_at`; deleting the note removes the row and the file. Neither is
claimed to erase backups or copies made outside this application.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from ..config import MAX_TRANSCRIPT_CHARS
from ..db.utils import utc_now
from ..models.schemas import VoiceNoteOut, VoiceNoteSummaryOut

logger = logging.getLogger(__name__)

# Only these content types are accepted. The map decides the stored extension,
# never the client-provided filename.
AUDIO_CONTENT_TYPES: dict[str, str] = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "audio/opus": ".opus",
}

_AUDIO_FILENAME_RE = re.compile(r"^[0-9a-f]{32}\.[a-z0-9]{2,5}$")
_SUMMARY_PREVIEW_CHARS = 160


class VoiceNoteError(Exception):
    """Base class for typed voice-note failures."""


class VoiceNoteNotFound(VoiceNoteError):
    pass


class VoiceNoteUnsupportedType(VoiceNoteError):
    pass


class VoiceNoteTooLarge(VoiceNoteError):
    pass


class VoiceNoteEmpty(VoiceNoteError):
    pass


class VoiceNoteStorageError(VoiceNoteError):
    pass


class VoiceNoteConflict(VoiceNoteError):
    pass


class VoiceNoteCorrectionProtected(VoiceNoteError):
    pass


class VoiceNoteAudioMissing(VoiceNoteError):
    pass


def _clean_content_type(raw: str | None) -> str:
    if not raw:
        return ""
    return raw.split(";", 1)[0].strip().lower()


def extension_for(content_type: str | None) -> str:
    extension = AUDIO_CONTENT_TYPES.get(_clean_content_type(content_type))
    if extension is None:
        raise VoiceNoteUnsupportedType(
            "Unsupported audio type. Supported types: "
            + ", ".join(sorted(AUDIO_CONTENT_TYPES))
        )
    return extension


def voice_notes_dir(settings: object) -> Path:
    return Path(getattr(settings, "voice_notes_dir"))


def audio_path(settings: object, filename: str) -> Path:
    """Resolve a stored audio file, refusing anything outside the store."""
    if not _AUDIO_FILENAME_RE.fullmatch(filename or ""):
        raise VoiceNoteStorageError("Stored audio reference is invalid.")
    root = voice_notes_dir(settings).resolve()
    candidate = (root / filename).resolve()
    if candidate.parent != root:
        raise VoiceNoteStorageError("Stored audio reference escapes the store.")
    return candidate


async def store_audio(
    settings: object,
    filename: str,
    chunks: AsyncIterator[bytes],
    max_bytes: int,
) -> int:
    """Stream an upload to disk with a hard size cap.

    A partial file is removed on failure or oversize. The final rename is
    atomic, so a completed metadata row never points at a partial file.
    """
    root = voice_notes_dir(settings)
    root.mkdir(parents=True, exist_ok=True)
    target = audio_path(settings, filename)
    if target.exists():
        raise VoiceNoteStorageError("Audio file already exists.")
    partial = target.with_name(f".{target.name}.part")
    total = 0
    try:
        with open(partial, "wb") as handle:
            async for chunk in chunks:
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise VoiceNoteTooLarge(
                        f"Audio exceeds the {max_bytes} byte limit."
                    )
                handle.write(chunk)
        if total == 0:
            raise VoiceNoteEmpty("The recording is empty.")
        os.replace(partial, target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return total


def remove_audio_file(settings: object, filename: str) -> bool:
    """Delete a stored audio file. Missing files are treated as already gone."""
    path = audio_path(settings, filename)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise VoiceNoteStorageError("Could not delete the audio file.") from exc


def probe_media(path: Path) -> tuple[bool | None, float | None]:
    """Best-effort format probe: (decodable, duration).

    `None` means ffprobe is unavailable, so the upload is not rejected on a
    tool that is not installed. A present ffprobe that cannot decode the file
    returns ``(False, None)``.
    """
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None, None
    try:
        completed = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    if completed.returncode != 0:
        return False, None
    try:
        value = float(completed.stdout.strip())
    except ValueError:
        return True, None
    return True, value if value > 0 else None


# ------------------------------------------------------------------ rows ---


def _flatten(text: str | None, limit: int | None = None) -> str | None:
    if text is None:
        return None
    flattened = " ".join(text.split())
    if not flattened:
        return None
    if limit is not None and len(flattened) > limit:
        return flattened[: limit - 1].rstrip() + "…"
    return flattened


def _audio_available(settings: object, row: sqlite3.Row) -> bool:
    if row["audio_deleted_at"]:
        return False
    try:
        return audio_path(settings, row["audio_filename"]).is_file()
    except VoiceNoteStorageError:
        return False


def to_summary(settings: object, row: sqlite3.Row) -> VoiceNoteSummaryOut:
    return VoiceNoteSummaryOut(
        id=row["id"],
        title=row["title"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        audio_available=_audio_available(settings, row),
        audio_deleted=row["audio_deleted_at"] is not None,
        audio_duration_seconds=row["audio_duration_seconds"],
        transcript_status=row["transcript_status"],
        transcript_source=row["transcript_source"],
        transcript_preview=_flatten(row["transcript"], _SUMMARY_PREVIEW_CHARS),
    )


def to_detail(settings: object, row: sqlite3.Row) -> VoiceNoteOut:
    return VoiceNoteOut(
        **to_summary(settings, row).model_dump(),
        audio_content_type=row["audio_content_type"],
        audio_bytes=row["audio_bytes"],
        audio_deleted_at=row["audio_deleted_at"],
        transcript=row["transcript"],
        machine_transcript=row["machine_transcript"],
        transcript_error=row["transcript_error"],
        transcript_language=row["transcript_language"],
        transcript_updated_at=row["transcript_updated_at"],
    )


def require_voice_note(conn: sqlite3.Connection, note_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM voice_notes WHERE id = ?", (note_id,)).fetchone()
    if row is None:
        raise VoiceNoteNotFound("Voice note not found.")
    return row


# --------------------------------------------------------------- lifecycle ---


def create_voice_note(
    conn: sqlite3.Connection,
    *,
    note_id: str,
    title: str,
    audio_filename: str,
    audio_content_type: str,
    audio_bytes: int,
    audio_duration_seconds: float | None,
) -> sqlite3.Row:
    now = utc_now()
    conn.execute(
        """INSERT INTO voice_notes
           (id, title, audio_filename, audio_content_type, audio_bytes,
            audio_duration_seconds, transcript_status, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (
            note_id,
            title,
            audio_filename,
            _clean_content_type(audio_content_type),
            audio_bytes,
            audio_duration_seconds,
            now,
            now,
        ),
    )
    return require_voice_note(conn, note_id)


def list_voice_notes(
    conn: sqlite3.Connection, limit: int, offset: int
) -> tuple[list[sqlite3.Row], int]:
    total = int(conn.execute("SELECT COUNT(*) AS n FROM voice_notes").fetchone()["n"])
    rows = conn.execute(
        """SELECT * FROM voice_notes
           ORDER BY created_at DESC, id ASC
           LIMIT ? OFFSET ?""",
        (limit, offset),
    ).fetchall()
    return rows, total


def delete_audio(conn: sqlite3.Connection, settings: object, note_id: str) -> sqlite3.Row:
    row = require_voice_note(conn, note_id)
    if row["audio_deleted_at"] is None:
        remove_audio_file(settings, row["audio_filename"])
        now = utc_now()
        conn.execute(
            "UPDATE voice_notes SET audio_deleted_at = ?, updated_at = ? WHERE id = ?",
            (now, now, note_id),
        )
    return require_voice_note(conn, note_id)


def delete_voice_note(conn: sqlite3.Connection, settings: object, note_id: str) -> None:
    row = require_voice_note(conn, note_id)
    remove_audio_file(settings, row["audio_filename"])
    conn.execute("DELETE FROM voice_notes WHERE id = ?", (note_id,))


def update_voice_note(
    conn: sqlite3.Connection,
    note_id: str,
    *,
    title: str | None = None,
    transcript: str | None = None,
) -> sqlite3.Row:
    row = require_voice_note(conn, note_id)
    if transcript is not None and row["transcript_status"] == "processing":
        raise VoiceNoteConflict(
            "Transcription is running; wait for it to finish before editing the transcript."
        )
    now = utc_now()
    if title is not None:
        conn.execute(
            "UPDATE voice_notes SET title = ?, updated_at = ? WHERE id = ?",
            (title, now, note_id),
        )
    if transcript is not None:
        conn.execute(
            """UPDATE voice_notes
               SET transcript = ?, transcript_source = 'user',
                   transcript_status = 'completed', transcript_error = NULL,
                   transcript_updated_at = ?, updated_at = ?
               WHERE id = ?""",
            (transcript, now, now, note_id),
        )
    return require_voice_note(conn, note_id)


# ------------------------------------------------------------ transcription ---


def prepare_transcription(
    conn: sqlite3.Connection, settings: object, note_id: str, force: bool
) -> sqlite3.Row:
    """Claim a note for background transcription, or explain why not.

    Refuses a concurrent run and refuses to replace a user correction unless
    `force` is set. Keeps any previous transcript until a new one succeeds.
    """
    row = require_voice_note(conn, note_id)
    if row["transcript_status"] == "processing":
        raise VoiceNoteConflict("Transcription is already running.")
    if row["transcript_source"] == "user" and not force:
        raise VoiceNoteCorrectionProtected(
            "This transcript was corrected by you. Confirm replacement to transcribe again."
        )
    if row["audio_deleted_at"] is not None:
        raise VoiceNoteAudioMissing("The audio for this note was deleted.")
    try:
        path = audio_path(settings, row["audio_filename"])
    except VoiceNoteStorageError as exc:
        raise VoiceNoteAudioMissing("The audio reference is invalid.") from exc
    if not path.is_file():
        raise VoiceNoteAudioMissing(
            "The audio file is missing. Import or record the note again."
        )
    now = utc_now()
    cursor = conn.execute(
        """UPDATE voice_notes
           SET transcript_status = 'processing', transcript_error = NULL, updated_at = ?
           WHERE id = ? AND transcript_status != 'processing'""",
        (now, note_id),
    )
    if cursor.rowcount == 0:
        raise VoiceNoteConflict("Transcription is already running.")
    return require_voice_note(conn, note_id)


def finish_transcription(
    conn: sqlite3.Connection,
    note_id: str,
    *,
    transcript: str,
    language: str | None,
) -> None:
    text = transcript.strip()
    if not text:
        fail_transcription(conn, note_id, "empty_transcript")
        return
    text = text[:MAX_TRANSCRIPT_CHARS]
    now = utc_now()
    # Only a job that still owns the `processing` state may publish. A note
    # deleted meanwhile (or edited after the claim) is never overwritten.
    conn.execute(
        """UPDATE voice_notes
           SET machine_transcript = ?, transcript = ?, transcript_source = 'machine',
               transcript_status = 'completed', transcript_error = NULL,
               transcript_language = ?, transcript_updated_at = ?, updated_at = ?
           WHERE id = ? AND transcript_status = 'processing'""",
        (text, text, language, now, now, note_id),
    )


def fail_transcription(
    conn: sqlite3.Connection, note_id: str, error_code: str
) -> None:
    now = utc_now()
    conn.execute(
        """UPDATE voice_notes
           SET transcript_status = 'failed', transcript_error = ?, updated_at = ?
           WHERE id = ? AND transcript_status = 'processing'""",
        (error_code[:200], now, note_id),
    )


def recover_interrupted_transcriptions(db_path: Path) -> int:
    """Mark in-process jobs left 'processing' by a restart as failed.

    Transcription runs in-process, so a restart cannot resume it. Reporting the
    failure is honest; the audio and any previous transcript are retained.
    """
    from ..db.connection import db_connection

    with db_connection(db_path) as conn:
        cursor = conn.execute(
            """UPDATE voice_notes
               SET transcript_status = 'failed',
                   transcript_error = 'interrupted_by_restart',
                   updated_at = ?
               WHERE transcript_status = 'processing'""",
            (utc_now(),),
        )
        count = cursor.rowcount
    if count:
        logger.info("Marked %d interrupted transcription(s) as failed.", count)
    return count
