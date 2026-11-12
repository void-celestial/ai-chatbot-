"""Read-only data export (Phase 3).

Exports the current local state as one JSON document: persona settings,
conversations, messages, every stored memory (including inactive/orphaned
records for audit), and memory history. The FTS index and migration backup
files are deliberately excluded because they are derived/backup copies, not
canonical records.

This endpoint never mutates the archive. It is local-only like the rest of the
API and does not claim complete erasure: backups and previously downloaded
exports can retain earlier copies.
"""

import json
import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..db.connection import db_connection
from ..db.utils import utc_now

logger = logging.getLogger(__name__)

router = APIRouter()

EXPORT_FORMAT = "companion-export"
EXPORT_FORMAT_VERSION = 1

_EXPORT_SCOPE_INCLUDES = [
    "persona settings",
    "conversations (all statuses)",
    "messages and their stored metadata",
    "all memories, including review/deleted/superseded/orphaned records",
    "memory history entries",
]
_EXPORT_SCOPE_EXCLUDES = [
    "SQLite FTS5 index contents (derived; rebuildable from memories)",
    "migration backup files under data/backups/",
    "application logs",
    "previously downloaded export files",
]


def _decode_json(raw, default):
    if not raw:
        return default
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return default
    return value


def _message_payload(row) -> dict:
    return {
        "id": row["id"],
        "conversation_id": row["conversation_id"],
        "seq": row["seq"],
        "role": row["role"],
        "content": row["content"],
        "created_at": row["created_at"],
        "model_tag": row["model_tag"],
        "generation_status": row["generation_status"],
        "metadata": _decode_json(row["metadata_json"], None),
    }


def _memory_payload(row) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "statement": row["statement"],
        "subject": row["subject"],
        "keywords": _decode_json(row["keywords_json"], []),
        "origin": row["origin"],
        "status": row["status"],
        "confidence": row["confidence"],
        "source_conversation_id": row["source_conversation_id"],
        "source_message_ids": _decode_json(row["source_message_ids_json"], []),
        "related_ids": _decode_json(row["related_ids_json"], None),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_confirmed_at": row["last_confirmed_at"],
    }


def _history_payload(row) -> dict:
    return {
        "id": row["id"],
        "memory_id": row["memory_id"],
        "event": row["event"],
        "value": _decode_json(row["value_json"], {}),
        "created_at": row["created_at"],
    }


@router.get("/api/export")
async def export_data(request: Request) -> JSONResponse:
    """Return one JSON document with the documented local export scope.

    Conversation/message rows reflect the current archive (a deleted
    conversation is no longer present, while memories derived from it remain in
    the export as `orphaned`). Source references in memories are the stored
    ids; validity is still determined by the memory API at read time.
    """
    settings = request.app.state.settings
    try:
        with db_connection(settings.db_path) as conn:
            persona_row = conn.execute(
                "SELECT * FROM persona WHERE id = 'default'"
            ).fetchone()
            conversations = [
                dict(row)
                for row in conn.execute(
                    "SELECT id, title, status, created_at, updated_at "
                    "FROM conversations ORDER BY created_at ASC, id ASC"
                ).fetchall()
            ]
            messages = [
                _message_payload(row)
                for row in conn.execute(
                    "SELECT * FROM messages ORDER BY conversation_id ASC, seq ASC"
                ).fetchall()
            ]
            memories = [
                _memory_payload(row)
                for row in conn.execute(
                    "SELECT * FROM memories ORDER BY created_at ASC, id ASC"
                ).fetchall()
            ]
            history = [
                _history_payload(row)
                for row in conn.execute(
                    "SELECT * FROM memory_history ORDER BY memory_id ASC, rowid ASC"
                ).fetchall()
            ]
    except (sqlite3.Error, ValueError) as exc:
        logger.warning("Data export failed: %s", exc.__class__.__name__)
        raise HTTPException(status_code=500, detail="Could not export local data.") from exc

    payload = {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": utc_now(),
        "scope": {
            "includes": _EXPORT_SCOPE_INCLUDES,
            "excludes": _EXPORT_SCOPE_EXCLUDES,
        },
        "counts": {
            "conversations": len(conversations),
            "messages": len(messages),
            "memories": len(memories),
            "memory_history": len(history),
        },
        "persona": dict(persona_row) if persona_row is not None else None,
        "conversations": conversations,
        "messages": messages,
        "memories": memories,
        "memory_history": history,
    }
    return JSONResponse(
        content=payload,
        headers={
            "Content-Disposition": 'attachment; filename="companion-export.json"',
            "Cache-Control": "no-store",
        },
    )
