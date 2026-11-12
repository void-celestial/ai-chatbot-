import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Query, Request

from ..db.connection import db_connection
from ..models.schemas import CreateConversationIn, ConversationOut, MessageOut
from ..db.utils import new_id, utc_now
from ..services.chat_service import (
    count_messages,
    delete_conversation_with_memories,
    get_conversation,
    list_messages,
)
from ..services.memory_service import MemoryPersistenceError

logger = logging.getLogger(__name__)

router = APIRouter()


# Read failures are reported with one sanitized sentence: no SQLite message,
# statement, or filesystem path is ever sent to the browser.
READ_FAILED_DETAIL = "Could not read stored conversations."


def _read_failed(action: str, exc: sqlite3.Error) -> HTTPException:
    logger.warning("Conversation %s failed: %s", action, exc.__class__.__name__)
    return HTTPException(status_code=500, detail=READ_FAILED_DETAIL)


@router.post("/api/conversations", response_model=ConversationOut, status_code=201)
async def create_conversation(payload: CreateConversationIn, request: Request) -> ConversationOut:
    settings = request.app.state.settings
    conversation_id = new_id()
    now = utc_now()
    title = payload.title or "New conversation"
    with db_connection(settings.db_path) as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, status, created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
            (conversation_id, title, now, now),
        )
    return ConversationOut(
        id=conversation_id, title=title, status="active", created_at=now, updated_at=now, message_count=0
    )


@router.get("/api/conversations", response_model=list[ConversationOut])
async def list_conversations(
    request: Request,
    limit: int = Query(default=200, ge=1, le=500),
) -> list[ConversationOut]:
    settings = request.app.state.settings
    try:
        with db_connection(settings.db_path) as conn:
            rows = conn.execute(
                """SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS message_count
                   FROM conversations c
                   WHERE c.status = 'active'
                   ORDER BY c.updated_at DESC, c.created_at DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise _read_failed("list", exc) from exc
    return [ConversationOut.model_validate(dict(row)) for row in rows]


@router.get("/api/conversations/{conversation_id}", response_model=ConversationOut)
async def get_conversation_endpoint(conversation_id: str, request: Request) -> ConversationOut:
    settings = request.app.state.settings
    try:
        with db_connection(settings.db_path) as conn:
            row = get_conversation(conn, conversation_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Conversation not found")
            count = count_messages(conn, conversation_id)
    except sqlite3.Error as exc:
        raise _read_failed("get", exc) from exc
    return ConversationOut(
        id=row["id"],
        title=row["title"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        message_count=count,
    )


@router.get("/api/conversations/{conversation_id}/messages", response_model=list[MessageOut])
async def list_conversation_messages(conversation_id: str, request: Request) -> list[MessageOut]:
    settings = request.app.state.settings
    try:
        with db_connection(settings.db_path) as conn:
            if get_conversation(conn, conversation_id) is None:
                raise HTTPException(status_code=404, detail="Conversation not found")
            return list_messages(conn, conversation_id)
    except sqlite3.Error as exc:
        raise _read_failed("messages", exc) from exc


@router.delete("/api/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request) -> None:
    """Delete a conversation and its messages; derived memories become orphaned.

    Explicit effects (Phase 3): the conversation row and every message in it are
    removed, while derived memories keep their row/content/history and transition
    to `orphaned` with a recorded reason, which excludes them from retrieval and
    prompt evidence. Backups and exports are not modified, so this is not
    complete erasure; the UI recommends exporting first.
    """
    settings = request.app.state.settings
    with db_connection(settings.db_path) as conn:
        try:
            orphaned = delete_conversation_with_memories(conn, conversation_id)
        except (sqlite3.Error, MemoryPersistenceError) as exc:
            logger.warning(
                "Conversation deletion failed (%s): %s",
                conversation_id,
                exc.__class__.__name__,
            )
            raise HTTPException(
                status_code=500, detail="Could not delete the conversation."
            ) from exc
    if orphaned is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    logger.info(
        "Conversation deleted (id=%s, orphaned_memories=%d)", conversation_id, orphaned
    )
