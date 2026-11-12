"""Chat orchestration.

Responsibilities:
- Persist user messages immediately (they survive generation failures).
- Assemble a budgeted prompt context from the current conversation.
- Stream the assistant reply from Ollama, persisting it only once it is real.
- Never store an empty or failed response as a successful assistant answer.
- On client disconnect, persist any partial reply as `interrupted`.
- Add retrieved memory evidence to the prompt without letting retrieval
  failures, missing evidence, or memory text itself change behavior.
"""

import json
import logging
import sqlite3
import time
from collections.abc import AsyncIterator
from typing import Any

from ..db.utils import new_id, utc_now
from ..models.schemas import MessageOut
from .memory_retrieval import retrieve_relevant_memories
from .memory_service import orphan_memories_for_conversation
from .ollama_client import OllamaClient, OllamaError
from .persona_service import get_persona
from .prompt_builder import build_messages, build_system_prompt, fit_memories_to_budget
from ..config import Settings

logger = logging.getLogger(__name__)

# Timing fields Ollama reports on the final stream chunk. Kept as a tuple so
# the log line and the payload stay in sync.
_TIMING_KEYS = (
    "load_duration",
    "prompt_eval_count",
    "prompt_eval_duration",
    "eval_count",
    "eval_duration",
)


class ConversationNotFound(Exception):
    pass


def _log_generation_timing(
    stats: dict[str, Any], started: float, first_delta_at: float | None
) -> None:
    """Log one metadata-only timing line per reply.

    Measured on the reference machine (2026-10-26): a cold model load is
    9.4-15.7s while prompt prefill is 0.16-0.8s and decode runs at 13-16
    tok/s, so `load_ms` is what distinguishes a slow first reply from a slow
    machine. Numbers only: this never logs message or memory text, and it is
    written to the local backend log, not sent anywhere.
    """

    def seconds(key: str) -> float:
        value = stats.get(key)
        return (value / 1e9) if isinstance(value, (int, float)) else 0.0

    eval_s = seconds("eval_duration")
    decode_rate = (stats.get("eval_count") or 0) / eval_s if eval_s > 0 else None
    now = time.perf_counter()
    logger.info(
        "Generation timing: ttft_ms=%.0f prefill_ms=%.0f decode_tok_per_s=%s "
        "load_ms=%.0f prompt_tokens=%s output_tokens=%s total_ms=%.0f",
        ((first_delta_at or now) - started) * 1000,
        seconds("prompt_eval_duration") * 1000,
        f"{decode_rate:.1f}" if decode_rate is not None else "n/a",
        seconds("load_duration") * 1000,
        stats.get("prompt_eval_count"),
        stats.get("eval_count"),
        (now - started) * 1000,
    )


def get_conversation(conn: sqlite3.Connection, conversation_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()


def count_messages(conn: sqlite3.Connection, conversation_id: str) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages WHERE conversation_id = ?", (conversation_id,)
    ).fetchone()
    return int(row["n"])


def _message_from_row(row: sqlite3.Row) -> MessageOut:
    """Decode a stored message row, including optional response metadata.

    Malformed metadata is treated as absent rather than breaking the message
    list; it is display metadata only and never prompt evidence.
    """
    data = dict(row)
    raw = data.pop("metadata_json", None)
    metadata = None
    if raw:
        try:
            decoded = json.loads(raw)
        except (TypeError, ValueError):
            decoded = None
        if isinstance(decoded, dict):
            metadata = decoded
    data["metadata"] = metadata
    return MessageOut.model_validate(data)


def _memory_evidence_metadata(memories: list) -> dict | None:
    """Bounded trace of the memories actually supplied to one reply.

    Stored in the existing `messages.metadata_json` column so the reply can
    show where continuity came from. Only validated retrieval records reach
    this function, so the trace cannot advertise a stale or mismatched source
    as verified evidence.
    """
    if not memories:
        return None
    return {
        "memory_evidence": [
            {
                "id": memory.id,
                "kind": memory.kind,
                "statement": memory.statement,
                "origin": memory.origin,
                "status": memory.status,
                "source_conversation_id": memory.source_conversation_id,
                "source_message_ids": list(memory.source_message_ids),
            }
            for memory in memories
        ]
    }


def insert_message(
    conn: sqlite3.Connection,
    conversation_id: str,
    role: str,
    content: str,
    model_tag: str | None = None,
    generation_status: str | None = None,
) -> MessageOut:
    message_id = new_id()
    created_at = utc_now()
    conn.execute(
        """INSERT INTO messages (id, conversation_id, role, content, created_at, model_tag, generation_status)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (message_id, conversation_id, role, content, created_at, model_tag, generation_status),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?", (created_at, conversation_id)
    )
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return _message_from_row(row)


def list_messages(conn: sqlite3.Connection, conversation_id: str) -> list[MessageOut]:
    rows = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? ORDER BY seq ASC",
        (conversation_id,),
    ).fetchall()
    return [_message_from_row(row) for row in rows]


def last_user_message(conn: sqlite3.Connection, conversation_id: str) -> MessageOut | None:
    row = conn.execute(
        "SELECT * FROM messages WHERE conversation_id = ? AND role = 'user' ORDER BY seq DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()
    return _message_from_row(row) if row else None


def previous_user_message(
    conn: sqlite3.Connection, conversation_id: str, before_seq: int
) -> MessageOut | None:
    """The user message immediately before `before_seq` (same conversation).

    Used only as bounded retrieval context for follow-up messages that contain
    no meaningful terms of their own. Never crosses conversations.
    """
    row = conn.execute(
        """SELECT * FROM messages
           WHERE conversation_id = ? AND role = 'user' AND seq < ?
           ORDER BY seq DESC LIMIT 1""",
        (conversation_id, before_seq),
    ).fetchone()
    return _message_from_row(row) if row else None


def delete_conversation_with_memories(
    conn: sqlite3.Connection, conversation_id: str
) -> int | None:
    """Delete one conversation and its messages, orphaning derived memories.

    Returns the number of orphaned memories, or None when the conversation does
    not exist. The conversation row and its messages are removed by the caller's
    transaction (messages cascade). Derived memories are retained and
    transitioned to `orphaned` with a history reason *before* deletion, because
    deleting the conversation would null their source conversation reference.
    No backup or export file is touched, and complete erasure is not claimed.
    """
    if get_conversation(conn, conversation_id) is None:
        return None
    orphaned = orphan_memories_for_conversation(conn, conversation_id)
    conn.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
    return orphaned


def _retrieve_memories_for_prompt(
    conn: sqlite3.Connection, settings: Settings, conversation_id: str
) -> list:
    """Retrieve memory evidence for the newest user message.

    Failure isolation: any retrieval/formatting problem returns an empty list
    ("no memory evidence"), never fabricated fallback memories and never an
    exception that could break streaming. Only metadata is logged — counts,
    reason codes, and memory ids, never memory or message text.
    """
    query = last_user_message(conn, conversation_id)
    if query is None:
        return []
    # Bounded same-conversation context for pronoun-only follow-ups. It is
    # used by retrieval only when the current message finds no evidence.
    context = previous_user_message(conn, conversation_id, query.seq)
    try:
        result = retrieve_relevant_memories(
            conn,
            settings,
            query.content,
            context_text=context.content if context is not None else None,
        )
    except Exception:  # defensive: retrieval must never break chat
        logger.warning(
            "Memory retrieval failed unexpectedly; replying without memory evidence."
        )
        return []
    if result.error is not None:
        logger.info(
            "Memory retrieval unavailable (%s); replying without memory evidence.",
            result.error,
        )
        return []
    try:
        selected = fit_memories_to_budget(
            result.memories,
            settings.memory_statement_prompt_chars,
            settings.memory_prompt_max_chars,
        )
    except Exception:  # defensive: formatting must never break chat
        logger.warning("Memory evidence could not be formatted; replying without it.")
        return []
    logger.info(
        "Memory retrieval: terms=%d considered=%d supplied=%d expanded=%s ids=%s",
        len(result.query_terms),
        result.considered,
        len(selected),
        result.query_expanded,
        [memory.id for memory in selected],
    )
    return selected


def sse_event(event: dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def stream_assistant_reply(
    conn: sqlite3.Connection,
    ollama: Any,
    settings: Settings,
    conversation_id: str,
    request: Any = None,
) -> AsyncIterator[dict[str, Any]]:
    """Generate and stream an assistant reply for the current conversation.

    `ollama` is injected (real client or test fake). `request`, when provided,
    is the FastAPI request used to detect client disconnects.
    """
    assistant_id = new_id()
    yield {"type": "assistant_started", "message_id": assistant_id}

    # Preflight: distinguish "Ollama down" from "configured model missing".
    try:
        installed = await ollama.list_models()
        if settings.model_tag not in installed:
            yield {
                "type": "error",
                "code": "model_not_found",
                "message": (
                    f"The configured model '{settings.model_tag}' is not available in Ollama. "
                    "Check `ollama list` and update the OLLAMA_MODEL_TAG setting."
                ),
            }
            return
    except OllamaError as exc:
        yield {"type": "error", "code": exc.code, "message": exc.message}
        return

    persona = get_persona(conn)
    history = list_messages(conn, conversation_id)
    memories = _retrieve_memories_for_prompt(conn, settings, conversation_id)
    evidence_metadata = _memory_evidence_metadata(memories)
    prompt = build_messages(
        history,
        settings.context_message_count,
        settings.context_max_chars,
        system_prompt=build_system_prompt(
            persona,
            memories,
            memory_section=True,
            statement_max_chars=settings.memory_statement_prompt_chars,
            memory_max_chars=settings.memory_prompt_max_chars,
            reply_style=settings.reply_style,
        ),
    )

    buffer = ""
    interrupted = False
    started = time.perf_counter()
    first_delta_at: float | None = None
    timing: dict[str, Any] = {}
    try:
        async for chunk in ollama.chat_stream(settings.model_tag, prompt, settings.num_ctx):
            if request is not None and await request.is_disconnected():
                interrupted = True
                break
            if chunk.get("done"):
                timing = {key: chunk[key] for key in _TIMING_KEYS if key in chunk}
            message = chunk.get("message") or {}
            delta = message.get("content")
            if not delta:
                # Thinking blocks / statistics-only chunks are not user-visible.
                continue
            if first_delta_at is None:
                first_delta_at = time.perf_counter()
            buffer += delta
            yield {"type": "delta", "content": delta}
    except OllamaError as exc:
        if request is not None and await request.is_disconnected():
            interrupted = True
        if interrupted:
            if buffer:  # keep the partial reply, clearly marked as unfinished
                _persist_assistant(
                    conn,
                    conversation_id,
                    assistant_id,
                    buffer,
                    settings.model_tag,
                    "interrupted",
                    metadata=evidence_metadata,
                )
            return
        yield {"type": "error", "code": exc.code, "message": exc.message}
        return

    if interrupted:
        if buffer:
            _persist_assistant(
                conn,
                conversation_id,
                assistant_id,
                buffer,
                settings.model_tag,
                "interrupted",
                metadata=evidence_metadata,
            )
        return

    if not buffer.strip():
        yield {
            "type": "error",
            "code": "generation_failed",
            "message": "The model returned no content. Try sending the message again.",
        }
        return

    _log_generation_timing(timing, started, first_delta_at)
    record = _persist_assistant(
        conn,
        conversation_id,
        assistant_id,
        buffer,
        settings.model_tag,
        "complete",
        metadata=evidence_metadata,
    )
    yield {"type": "done", "message": record.model_dump(mode="json")}


def _persist_assistant(
    conn: sqlite3.Connection,
    conversation_id: str,
    assistant_id: str,
    content: str,
    model_tag: str,
    status: str,
    metadata: dict | None = None,
) -> MessageOut:
    created_at = utc_now()
    conn.execute(
        """INSERT INTO messages (
               id, conversation_id, role, content, created_at, model_tag,
               generation_status, metadata_json
           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            assistant_id,
            conversation_id,
            "assistant",
            content,
            created_at,
            model_tag,
            status,
            json.dumps(metadata, ensure_ascii=False) if metadata else None,
        ),
    )
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE id = ?", (created_at, conversation_id)
    )
    row = conn.execute("SELECT * FROM messages WHERE id = ?", (assistant_id,)).fetchone()
    return _message_from_row(row)
