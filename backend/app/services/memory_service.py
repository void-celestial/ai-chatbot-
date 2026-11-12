"""Structured-memory storage service (Phase 2 storage foundation).

Layer B of `docs/MEMORY_SPEC.md`: derived memories with provenance, kept
strictly separate from the source message archive and from persona settings
(the persona service never touches this module and vice versa).

Scope: storage, lifecycle management, and the read/transition helpers behind
the memory-management API (Phase 2, Step 6). Extraction, ranking/retrieval, and
prompt injection live in their own services. When retrieved context is
injected into prompts, every statement handled here is untrusted evidence,
never a system instruction.

API-facing policy added in Step 6 (all writes still go through the existing
lifecycle function, so history/audit behavior is unchanged):
- confirm: `review` or `accepted` → `confirmed` (provenance untouched)
- reject: `review` → `deleted` (a rejected candidate is excluded from retrieval
  exactly like a deleted memory; the history entry records the reason)
- correction: allowed only for `accepted`/`review`/`confirmed`
- delete: soft delete to `deleted`, idempotent; rows and history are retained

There is no separate `rejected` status in the schema. Rejection is represented
as a soft delete with an explicit reason in `memory_history`, which keeps the
CHECK constraints untouched while guaranteeing the candidate is never
retrieved again.

Schema: migration 0002 (`memories` + `memory_history`); no new migration.

Provenance and integrity rules enforced here:
- At least one provenance reference is required (conversation or message).
- `source_conversation_id`, when supplied, must reference an existing
  conversation row.
- Every source message id must reference an existing message row; when a
  source conversation is set, every referenced message must belong to it.
- At read time, a message-bearing memory is valid only while every declared
  message still exists and belongs to the declared conversation. A surviving
  conversation alone does not make a deleted message reference valid.
- Legacy conversation-only records (no message references) remain valid while
  their declared conversation exists. A record with no usable source is
  invalid.
- References are never invented: unknown ids are rejected, not coerced.
- `origin` is caller-declared; nothing here promotes an inferred origin or
  an AI-generated statement to user-stated/confirmed status.
- Corrections and status transitions append to `memory_history` and never
  destroy prior values. Deleting sets `status='deleted'` and keeps the row so
  history stays auditable.

History events: the `memory_history.event` CHECK allows
(created, corrected, merged, conflicted). 'merged'/'conflicted' are produced
by later dedup/conflict steps; status transitions in this step are recorded
as 'corrected' events carrying explicit status_from/status_to metadata.
"""

import json
import sqlite3

from ..config import (
    MAX_MEMORY_SOURCE_MESSAGE_REFS,
)
from ..db.utils import new_id, utc_now
from ..models.schemas import (
    MemoryCorrectionIn,
    MemoryCreateIn,
    MemoryHistoryOut,
    MemoryOut,
    MemoryProvenanceOut,
    MemoryStatusIn,
)

CREATE_TIME_STATUSES = frozenset({"accepted", "review", "confirmed"})

# API lifecycle policy. The database CHECK allows any status transition; these
# sets keep the management endpoints from resurrecting terminal records or
# accepting arbitrary client-chosen status values.
CONFIRMABLE_STATUSES = frozenset({"review", "accepted"})
REJECTABLE_STATUSES = frozenset({"review"})
# A memory the user confirms as outdated stays in the audit trail but is
# excluded from retrieval (see memory_retrieval.RETRIEVABLE_STATUSES).
SUPERSEDABLE_STATUSES = frozenset({"accepted", "confirmed"})
EDITABLE_STATUSES = frozenset({"accepted", "review", "confirmed"})

_SQL_CHUNK = 400

USER_STATED_DEFAULT_STATUS = "accepted"
INFERRED_DEFAULT_STATUS = "review"
USER_STATED_DEFAULT_CONFIDENCE = 1.0
INFERRED_DEFAULT_CONFIDENCE = 0.5


class MemoryNotFound(LookupError):
    """Raised when an operation targets a memory id that does not exist."""


class MemorySourceError(ValueError):
    """Raised when provenance references are missing, unknown, or mismatched."""


class MemoryPersistenceError(Exception):
    """Raised when a memory write cannot be completed (non-validation)."""


class MemoryTransitionError(Exception):
    """Raised when an API lifecycle operation is not allowed for the status."""


# ---------------------------------------------------------------- reading ---

def get_memory(conn: sqlite3.Connection, memory_id: str) -> MemoryOut | None:
    """Return the stored memory with this id (any status), or None."""
    row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return _row_to_out(row) if row is not None else None


def require_memory(conn: sqlite3.Connection, memory_id: str) -> MemoryOut:
    """Like `get_memory`, but raises MemoryNotFound instead of returning None."""
    memory = get_memory(conn, memory_id)
    if memory is None:
        raise MemoryNotFound(memory_id)
    return memory


def get_memory_history(
    conn: sqlite3.Connection, memory_id: str, limit: int | None = None
) -> list[MemoryHistoryOut]:
    """Chronological history entries for a memory (also works when deleted).

    With `limit`, only the newest entries are read (bounded query) and then
    returned oldest-first, so the caller still sees a chronological window.
    """
    require_memory(conn, memory_id)
    try:
        if limit is None:
            rows = conn.execute(
                "SELECT * FROM memory_history WHERE memory_id = ? ORDER BY rowid ASC",
                (memory_id,),
            ).fetchall()
        else:
            # Newest N entries, then reversed for chronological output.
            newest_first = conn.execute(
                """SELECT * FROM memory_history
                   WHERE memory_id = ? ORDER BY rowid DESC LIMIT ?""",
                (memory_id, max(0, int(limit))),
            ).fetchall()
            rows = list(reversed(newest_first))
    except sqlite3.Error as exc:
        raise MemoryPersistenceError("Could not read the memory history.") from exc
    return [
        MemoryHistoryOut(
            id=row["id"],
            memory_id=row["memory_id"],
            event=row["event"],
            value=json.loads(row["value_json"]),
            created_at=row["created_at"],
        )
        for row in rows
    ]


def _row_to_out(row: sqlite3.Row) -> MemoryOut:
    return MemoryOut(
        id=row["id"],
        kind=row["kind"],
        statement=row["statement"],
        subject=row["subject"],
        keywords=json.loads(row["keywords_json"]),
        origin=row["origin"],
        status=row["status"],
        confidence=float(row["confidence"]),
        source_conversation_id=row["source_conversation_id"],
        source_message_ids=json.loads(row["source_message_ids_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_confirmed_at=row["last_confirmed_at"],
    )


# ---------------------------------------------------------------- creating ---

def create_memory(conn: sqlite3.Connection, payload: MemoryCreateIn) -> MemoryOut:
    """Store a new memory with validated provenance and a 'created' history entry.

    The memory row and its history entry are written in one transaction (the
    caller's `db_connection` commits once at the end), so they cannot end up
    half-applied.
    """
    source_message_ids = _normalized_source_ids(payload.source_message_ids)
    _validate_provenance(conn, payload.source_conversation_id, source_message_ids)

    if payload.status is not None:
        status = payload.status
    elif payload.origin == "user_stated":
        status = USER_STATED_DEFAULT_STATUS
    else:
        status = INFERRED_DEFAULT_STATUS

    if payload.confidence is not None:
        confidence = payload.confidence
    elif payload.origin == "user_stated":
        confidence = USER_STATED_DEFAULT_CONFIDENCE
    else:
        confidence = INFERRED_DEFAULT_CONFIDENCE

    memory_id = new_id()
    created_at = utc_now()
    keywords_json = json.dumps(payload.keywords, ensure_ascii=False)
    source_ids_json = json.dumps(source_message_ids, ensure_ascii=False)

    try:
        conn.execute(
            """INSERT INTO memories (
                   id, kind, statement, subject, keywords_json, origin, status,
                   confidence, source_conversation_id, source_message_ids_json,
                   created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                memory_id,
                payload.kind,
                payload.statement,
                payload.subject,
                keywords_json,
                payload.origin,
                status,
                confidence,
                payload.source_conversation_id,
                source_ids_json,
                created_at,
                created_at,
            ),
        )
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        memory = _row_to_out(row)
        _record_history(conn, memory_id, "created", {"memory": memory.model_dump(mode="json")})
    except sqlite3.Error as exc:
        raise MemoryPersistenceError(
            "Could not save the memory record."
        ) from exc
    return memory


def _normalized_source_ids(raw_ids: list[str]) -> list[str]:
    """Strip, drop empties, and de-duplicate provenance ids in order."""
    normalized: list[str] = []
    for raw in raw_ids:
        stripped = raw.strip()
        if stripped and stripped not in normalized:
            normalized.append(stripped)
        if len(normalized) > MAX_MEMORY_SOURCE_MESSAGE_REFS:
            raise MemorySourceError(
                f"At most {MAX_MEMORY_SOURCE_MESSAGE_REFS} source messages may be referenced."
            )
    return normalized


def _validate_provenance(
    conn: sqlite3.Connection,
    source_conversation_id: str | None,
    source_message_ids: list[str],
) -> None:
    """Reject memories whose provenance cannot be traced to real rows."""
    if source_conversation_id is None and not source_message_ids:
        raise MemorySourceError(
            "A memory must reference at least one source: a source conversation "
            "and/or at least one source message."
        )

    if source_conversation_id is not None:
        exists = conn.execute(
            "SELECT 1 FROM conversations WHERE id = ?", (source_conversation_id,)
        ).fetchone()
        if exists is None:
            raise MemorySourceError(
                "Source conversation does not exist in the local archive."
            )

    for message_id in source_message_ids:
        row = conn.execute(
            "SELECT conversation_id FROM messages WHERE id = ?", (message_id,)
        ).fetchone()
        if row is None:
            raise MemorySourceError(
                "Source message does not exist in the local archive."
            )
        if (
            source_conversation_id is not None
            and row["conversation_id"] != source_conversation_id
        ):
            raise MemorySourceError(
                "Source message belongs to a different conversation than the "
                "declared source conversation."
            )


# ------------------------------------------------------------- correcting ---

def correct_memory(
    conn: sqlite3.Connection, memory_id: str, payload: MemoryCorrectionIn
) -> MemoryOut:
    """Apply a user correction to memory content, preserving the previous value.

    Provenance (conversation/message ids), kind, and origin intentionally
    cannot be altered by a correction. The prior snapshot is appended to
    `memory_history` in the same transaction.
    """
    current = require_memory(conn, memory_id)
    if not payload.model_fields_set:
        raise ValueError("A correction must supply at least one field to change.")

    values = {
        "statement": current.statement,
        "subject": current.subject,
        "keywords": list(current.keywords),
    }
    for field in payload.model_fields_set:
        values[field] = getattr(payload, field)

    before = current.model_dump(mode="json")
    try:
        conn.execute(
            """UPDATE memories
               SET statement = ?, subject = ?, keywords_json = ?, updated_at = ?
               WHERE id = ?""",
            (
                values["statement"],
                values["subject"],
                json.dumps(values["keywords"], ensure_ascii=False),
                utc_now(),
                memory_id,
            ),
        )
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        after = _row_to_out(row)
        _record_history(
            conn,
            memory_id,
            "corrected",
            {"before": before, "after": after.model_dump(mode="json")},
        )
    except sqlite3.Error as exc:
        raise MemoryPersistenceError(
            "Could not save the memory correction."
        ) from exc
    return after


def update_memory_status(
    conn: sqlite3.Connection, memory_id: str, payload: MemoryStatusIn
) -> MemoryOut:
    """Transition a memory's lifecycle status, recording the transition.

    Setting status 'deleted' keeps the row (history is retained and the record
    stays inspectable); later retrieval steps must exclude deleted/superseded/
    conflicted/orphaned statuses. Confirming sets `last_confirmed_at`.
    Re-affirming an identical status is a no-op and writes no history entry.
    """
    current = require_memory(conn, memory_id)
    if payload.status == current.status:
        return current  # idempotent: nothing changed, nothing to record

    status_from = current.status
    status_to = payload.status
    now = utc_now()
    last_confirmed_at = now if status_to == "confirmed" else current.last_confirmed_at
    try:
        conn.execute(
            """UPDATE memories
               SET status = ?, updated_at = ?, last_confirmed_at = ?
               WHERE id = ?""",
            (status_to, now, last_confirmed_at, memory_id),
        )
        row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        after = _row_to_out(row)
        _record_history(
            conn,
            memory_id,
            "corrected",
            {
                "status_from": status_from,
                "status_to": status_to,
                "reason": payload.reason,
            },
        )
    except sqlite3.Error as exc:
        raise MemoryPersistenceError(
            "Could not update the memory status."
        ) from exc
    return after


# ---------------------------------------------------------------- history ---

def _record_history(
    conn: sqlite3.Connection, memory_id: str, event: str, value: dict
) -> None:
    """Append one history entry (must be called in the same transaction)."""
    conn.execute(
        """INSERT INTO memory_history (id, memory_id, event, value_json, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (new_id(), memory_id, event, json.dumps(value, ensure_ascii=False), utc_now()),
    )

# ----------------------------------------------------- management-API reads ---

def list_memories(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    kind: str | None = None,
    origin: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[MemoryOut], int]:
    """One deterministic page of memories plus the total for those filters.

    Filter values are always bound parameters; the only interpolated SQL is
    the fixed column name selected from `_FILTER_COLUMNS`. Ordering is stable
    (`updated_at DESC, id ASC`), so pagination cannot duplicate or skip rows
    between pages of an unchanged store. All statuses (including `deleted`)
    are listable so a review client can audit them.
    """
    clauses: list[str] = []
    params: list[object] = []
    # Column names are fixed literals; every value is a bound parameter.
    for column, value in (("status", status), ("kind", kind), ("origin", origin)):
        if value is not None:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    try:
        total_row = conn.execute(
            f"SELECT COUNT(*) AS n FROM memories{where}", params
        ).fetchone()
        rows = conn.execute(
            f"""SELECT * FROM memories{where}
                ORDER BY updated_at DESC, id ASC
                LIMIT ? OFFSET ?""",
            (*params, max(0, int(limit)), max(0, int(offset))),
        ).fetchall()
    except sqlite3.Error as exc:
        raise MemoryPersistenceError("Could not read stored memories.") from exc
    return [_row_to_out(row) for row in rows], int(total_row["n"])


def _existing_ids(
    conn: sqlite3.Connection, table: str, ids: set[str]
) -> set[str]:
    """Existing ids for a fixed table name (never interpolated from input)."""
    if not ids:
        return set()
    existing: set[str] = set()
    ordered = sorted(ids)
    for start in range(0, len(ordered), _SQL_CHUNK):
        chunk = ordered[start : start + _SQL_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id FROM {table} WHERE id IN ({placeholders})", chunk
        ).fetchall()
        existing.update(str(row["id"]) for row in rows)
    return existing


def _existing_message_sources(
    conn: sqlite3.Connection, ids: set[str]
) -> dict[str, str]:
    """Existing message ids mapped to their actual conversation ids."""
    if not ids:
        return {}
    existing: dict[str, str] = {}
    ordered = sorted(ids)
    for start in range(0, len(ordered), _SQL_CHUNK):
        chunk = ordered[start : start + _SQL_CHUNK]
        placeholders = ", ".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, conversation_id FROM messages WHERE id IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            existing[str(row["id"])] = str(row["conversation_id"])
    return existing


def resolve_source_references(
    source_conversation_id: str | None,
    source_message_ids: list[str] | tuple[str, ...],
    existing_conversations: set[str],
    existing_message_conversations: dict[str, str],
) -> tuple[str | None, list[str], bool]:
    """Apply the storage/retrieval provenance policy to one memory.

    Policy (conservative, backward compatible):
    - Message-bearing memories require every declared message to still exist.
      When a source conversation is declared, that conversation must also
      exist and every message must belong to it. A missing or mismatched
      message makes the whole memory grounded-evidence-invalid rather than
      silently dropping the stale reference.
    - Legacy records with no message-level references are valid only while
      their declared conversation exists. This preserves conversation-only
      records created before message-level provenance was required.
    - A record with neither a conversation nor messages is invalid.
    - Message-only records are valid when all declared messages exist; no
      current conversation is required, so cross-conversation retrieval keeps
      working.

    Returns the validated conversation id, the still-valid message ids, and
    whether the memory has valid grounded evidence. Surviving message ids are
    returned even when the memory as a whole is invalid, so the API can show
    what is left without pretending the stale reference is valid.
    """
    declared_messages = list(source_message_ids)
    conversation_declared = source_conversation_id is not None
    conversation_valid = bool(
        source_conversation_id and source_conversation_id in existing_conversations
    )
    validated_messages = [
        message_id
        for message_id in declared_messages
        if message_id in existing_message_conversations
        and (
            not conversation_declared
            or existing_message_conversations[message_id] == source_conversation_id
        )
    ]

    if declared_messages:
        valid = len(validated_messages) == len(declared_messages) and (
            not conversation_declared or conversation_valid
        )
    else:
        # Legacy conversation-only provenance.
        valid = conversation_declared and conversation_valid

    return (
        source_conversation_id if conversation_valid else None,
        validated_messages,
        valid,
    )


def memory_provenance(
    conn: sqlite3.Connection, memories: list[MemoryOut]
) -> dict[str, MemoryProvenanceOut]:
    """Validate source references for a batch of memories in two queries.

    Returns only references that still exist *and* satisfy the declared
    conversation relationship. A stale message makes `valid` false even when
    the conversation survives, so callers cannot mistake a deleted source
    message for working evidence. Keeps list responses free of N+1 lookups.
    """
    if not memories:
        return {}
    declared_conversations = {
        memory.source_conversation_id
        for memory in memories
        if memory.source_conversation_id
    }
    declared_messages = {
        message_id for memory in memories for message_id in memory.source_message_ids
    }
    try:
        conversations = _existing_ids(conn, "conversations", declared_conversations)
        message_sources = _existing_message_sources(conn, declared_messages)
    except sqlite3.Error as exc:
        raise MemoryPersistenceError("Could not read memory sources.") from exc

    provenance: dict[str, MemoryProvenanceOut] = {}
    for memory in memories:
        conversation_id, message_ids, valid = resolve_source_references(
            memory.source_conversation_id,
            memory.source_message_ids,
            conversations,
            message_sources,
        )
        provenance[memory.id] = MemoryProvenanceOut(
            conversation_id=conversation_id,
            message_ids=message_ids,
            valid=valid,
        )
    return provenance


# ------------------------------------------------- management-API lifecycle ---

def ensure_memory_editable(memory: MemoryOut) -> None:
    """Reject corrections of memories in terminal lifecycle states."""
    if memory.status not in EDITABLE_STATUSES:
        raise MemoryTransitionError(
            "Only accepted, review, or confirmed memories can be corrected."
        )


def confirm_memory(
    conn: sqlite3.Connection, memory_id: str, reason: str | None = None
) -> MemoryOut:
    """Confirm an accepted or review-pending memory.

    Origin and provenance are never upgraded by confirmation; an inferred
    memory stays `inferred` even after the user confirms it.
    """
    current = require_memory(conn, memory_id)
    if current.status not in CONFIRMABLE_STATUSES:
        raise MemoryTransitionError(
            "Only accepted or review-pending memories can be confirmed."
        )
    return update_memory_status(
        conn, memory_id, MemoryStatusIn(status="confirmed", reason=reason)
    )


def reject_memory(
    conn: sqlite3.Connection, memory_id: str, reason: str | None = None
) -> MemoryOut:
    """Reject a review-pending candidate.

    The schema has no `rejected` status, so rejection is recorded as a soft
    delete with an explicit reason. Retrieval only ever considers accepted or
    confirmed records, so the rejected candidate cannot be injected.
    """
    current = require_memory(conn, memory_id)
    if current.status not in REJECTABLE_STATUSES:
        raise MemoryTransitionError(
            "Only memories awaiting review can be rejected."
        )
    return update_memory_status(
        conn,
        memory_id,
        MemoryStatusIn(status="deleted", reason=reason or "Rejected during review"),
    )


def supersede_memory(
    conn: sqlite3.Connection, memory_id: str, reason: str | None = None
) -> MemoryOut:
    """Retire an accepted/confirmed memory because it is now outdated.

    The record, content, provenance, and history are retained; the status
    change excludes it from retrieval and prompt evidence. This is how changing
    personal circumstances are handled without silently rewriting the past.
    Review candidates use reject instead, and terminal statuses are not
    resurrected.
    """
    current = require_memory(conn, memory_id)
    if current.status not in SUPERSEDABLE_STATUSES:
        raise MemoryTransitionError(
            "Only accepted or confirmed memories can be marked outdated."
        )
    return update_memory_status(
        conn,
        memory_id,
        MemoryStatusIn(status="superseded", reason=reason or "Marked outdated by user"),
    )


def delete_memory(
    conn: sqlite3.Connection, memory_id: str, reason: str = "Deleted by user"
) -> MemoryOut:
    """Soft-delete a memory, retaining the row and its audit history.

    Idempotent: deleting an already-deleted memory returns it unchanged and
    writes no extra history entry. Source messages and conversations are never
    touched; the row disappearing from retrieval is what "deleted" means here
    (backups may retain earlier copies).
    """
    current = require_memory(conn, memory_id)
    if current.status == "deleted":
        return current
    return update_memory_status(
        conn, memory_id, MemoryStatusIn(status="deleted", reason=reason)
    )


# ------------------------------------------------- conversation consequences ---

# Statuses that are already excluded from retrieval or represent an explicit
# user decision: orphaning must not overwrite a deletion/supersede/conflict.
_ORPHAN_SKIP_STATUSES = frozenset({"deleted", "orphaned", "superseded", "conflicted"})

DEFAULT_CONVERSATION_DELETION_REASON = "Source conversation deleted"


def orphan_memories_for_conversation(
    conn: sqlite3.Connection,
    conversation_id: str,
    reason: str = DEFAULT_CONVERSATION_DELETION_REASON,
) -> int:
    """Mark memories derived from a conversation as `orphaned` before deletion.

    Called by conversation deletion **before** the conversation row is removed
    (which would set `source_conversation_id` to NULL and cascade-delete the
    messages). The memory row, content, and history are retained so the user can
    still inspect or export what was remembered; the `orphaned` status and the
    read-time provenance check keep it out of retrieval and prompt evidence.

    Only memories that actually cite this conversation or one of its messages
    are affected. Deleted/superseded/conflicted records are left untouched (their
    history already explains why they are inactive). Returns the number of
    memories transitioned. All writes use the caller's transaction.
    """
    message_rows = conn.execute(
        "SELECT id FROM messages WHERE conversation_id = ?", (conversation_id,)
    ).fetchall()
    conversation_message_ids = {str(row["id"]) for row in message_rows}

    rows = conn.execute(
        """SELECT id, status, source_conversation_id, source_message_ids_json
           FROM memories"""
    ).fetchall()
    orphaned = 0
    for row in rows:
        if row["status"] in _ORPHAN_SKIP_STATUSES:
            continue
        links_conversation = row["source_conversation_id"] == conversation_id
        if not links_conversation and conversation_message_ids:
            try:
                stored_ids = json.loads(row["source_message_ids_json"])
            except (TypeError, ValueError):
                stored_ids = []
            if isinstance(stored_ids, list):
                links_conversation = any(
                    isinstance(message_id, str)
                    and message_id in conversation_message_ids
                    for message_id in stored_ids
                )
        if not links_conversation:
            continue
        update_memory_status(
            conn, row["id"], MemoryStatusIn(status="orphaned", reason=reason)
        )
        orphaned += 1
    return orphaned
