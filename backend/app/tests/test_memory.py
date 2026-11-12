"""Phase 2 Step 3 tests: structured memory storage foundation.

Covers creation/retrieval, provenance validation (including rejection of
nonexistent or mismatched source references), input validation and boundary
conditions, corrections and status lifecycle with history preservation,
transaction rollback, and persistence across reinitialization.
"""

import json
import sqlite3
from contextlib import closing

import pytest

import backend.app.services.memory_service as memory_service
from backend.app.config import MAX_MEMORY_STATEMENT_CHARS
from backend.app.db.connection import db_connection
from backend.app.db.migration import migrate
from backend.app.db.connection import init_db
from backend.app.models.schemas import (
    MemoryCorrectionIn,
    MemoryCreateIn,
    MemoryStatusIn,
)
from backend.app.services.memory_service import (
    MemoryNotFound,
    MemoryPersistenceError,
    MemorySourceError,
    correct_memory,
    create_memory,
    get_memory,
    get_memory_history,
    require_memory,
    update_memory_status,
)


def migrated_db(tmp_path) -> object:
    """Fresh database with base schema plus migrations (persona table seeded)."""
    db_path = tmp_path / "memory.db"
    init_db(db_path)
    migrate(db_path, backup=False)
    return db_path


def seed_conversation(db_path, conversation_id: str = "conv1") -> str:
    """Insert a conversation (and optionally messages) directly; return its id."""
    with closing(sqlite3.connect(db_path)) as raw:
        raw.execute(
            "INSERT INTO conversations (id, title, status, created_at, updated_at)"
            " VALUES (?, 'Seed chat', 'active', 't0', 't0')",
            (conversation_id,),
        )
        raw.commit()
    return conversation_id


def seed_message(db_path, message_id: str, conversation_id: str, content: str) -> None:
    with closing(sqlite3.connect(db_path)) as raw:
        raw.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at)"
            " VALUES (?, ?, 'user', ?, 't1')",
            (message_id, conversation_id, content),
        )
        raw.commit()


def stated(statement: str, **overrides) -> MemoryCreateIn:
    base = dict(
        kind="fact",
        statement=statement,
        origin="user_stated",
        source_conversation_id="conv1",
        source_message_ids=["msg1"],
    )
    return MemoryCreateIn(**{**base, **overrides})


# ------------------------------------------------------------- creation -----

def test_create_and_retrieve_memory_roundtrip(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "My sister Mara lives in Oslo")

    payload = stated(
        "Mara lives in Oslo.",
        subject="Mara",
        keywords=["mara", "oslo"],
        confidence=0.9,
    )
    with db_connection(db_path) as conn:
        created = create_memory(conn, payload)
        fetched = get_memory(conn, created.id)

        assert fetched == created
        assert fetched.kind == "fact"
        assert fetched.statement == "Mara lives in Oslo."
        assert fetched.subject == "Mara"
        assert fetched.keywords == ["mara", "oslo"]
        assert fetched.origin == "user_stated"
        assert fetched.status == "accepted"
        assert fetched.confidence == 0.9
        assert fetched.source_conversation_id == "conv1"
        assert fetched.source_message_ids == ["msg1"]
        assert fetched.created_at.endswith("Z")
        assert len(fetched.id) == 32  # new_id() = uuid4().hex


def test_user_stated_defaults_to_accepted_with_full_confidence(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "I prefer tea over coffee")

    with db_connection(db_path) as conn:
        memory = create_memory(
            conn,
            stated("The user prefers tea.", kind="preference"),
        )
        assert memory.status == "accepted"
        assert memory.confidence == 1.0


def test_inferred_defaults_to_review_with_low_confidence(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "Sounds like you enjoy hiking")

    with db_connection(db_path) as conn:
        memory = create_memory(
            conn,
            stated(
                "The user might enjoy hiking.",
                kind="preference",
                origin="inferred",
            ),
        )
        # Uncertain material must not enter storage as accepted fact.
        assert memory.status == "review"
        assert memory.confidence == 0.5


def test_stable_id_differs_between_memories(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")
    with db_connection(db_path) as conn:
        first = create_memory(conn, stated("First memory"))
        second = create_memory(conn, stated("Second memory"))
        assert first.id != second.id


# ------------------------------------------------------------ provenance ----

def test_rejects_nonexistent_source_message(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    with db_connection(db_path) as conn:
        with pytest.raises(MemorySourceError):
            create_memory(conn, stated("Untraceable claim", source_message_ids=["no-such-msg"]))


def test_rejects_message_from_a_different_conversation(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path, "conv1")
    seed_conversation(db_path, "conv2")
    seed_message(db_path, "msg-elsewhere", "conv2", "From another chat")

    with db_connection(db_path) as conn:
        with pytest.raises(MemorySourceError):
            create_memory(
                conn,
                stated("Cross-conversation claim", source_message_ids=["msg-elsewhere"]),
            )


def test_allows_message_reference_without_conversation_reference(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path, "conv1")
    seed_message(db_path, "msg1", "conv1", "seeds")

    with db_connection(db_path) as conn:
        memory = create_memory(
            conn,
            stated("Message-only provenance", source_conversation_id=None),
        )
        assert memory.source_conversation_id is None
        assert memory.source_message_ids == ["msg1"]


def test_rejects_nonexistent_source_conversation(tmp_path):
    db_path = migrated_db(tmp_path)
    with db_connection(db_path) as conn:
        with pytest.raises(MemorySourceError):
            create_memory(
                conn,
                stated(
                    "No such conversation",
                    source_conversation_id="ghost-conv",
                    source_message_ids=[],
                ),
            )


def test_requires_at_least_one_provenance_reference(tmp_path):
    db_path = migrated_db(tmp_path)
    with db_connection(db_path) as conn:
        with pytest.raises(MemorySourceError):
            create_memory(
                conn,
                stated("Floating claim", source_conversation_id=None, source_message_ids=[]),
            )


def test_duplicate_source_ids_are_normalized(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")

    with db_connection(db_path) as conn:
        memory = create_memory(
            conn,
            stated("Deduped refs", source_message_ids=["msg1", " msg1 ", "msg1"]),
        )
        assert memory.source_message_ids == ["msg1"]


# ---------------------------------------------------------- validation -----

@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": "story_fact"},                    # kind outside migration enum
        {"origin": "ai_generated"},                # origin outside schema enum
        {"status": "superseded"},                  # lifecycle status not settable at creation
        {"confidence": 1.5},                       # confidence outside [0, 1]
        {"confidence": -0.1},
        {"keywords": ["x" * 51]},                  # keyword too long
        {"keywords": ["a", "b", "c" * 51]},       # one bad keyword among valid ones
        {"source_message_ids": ["m"] * 51},        # too many source refs
    ],
)
def test_invalid_memory_creation_rejected(overrides, tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")
    with db_connection(db_path) as conn:
        with pytest.raises(ValueError):
            create_memory(conn, stated("Anything", **overrides))


def test_blank_statement_rejected(tmp_path):
    db_path = migrated_db(tmp_path)
    with db_connection(db_path) as conn:
        with pytest.raises(ValueError):
            create_memory(conn, stated("   "))


def test_statement_over_limit_rejected(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")
    with db_connection(db_path) as conn:
        max_chars = MAX_MEMORY_STATEMENT_CHARS
        create_memory(conn, stated("x" * max_chars))  # at limit: accepted

        with pytest.raises(ValueError):
            create_memory(conn, stated("x" * (max_chars + 1)))


def test_keyword_deduplication_is_case_insensitive(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")
    with db_connection(db_path) as conn:
        memory = create_memory(conn, stated("Keywords collapsed", keywords=["Mara", " mara ", "OSLO"]))
        assert memory.keywords == ["Mara", "OSLO"]


# -------------------------------------------------------- corrections -------

def test_correction_updates_values_and_history_preserves_original(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")
    with db_connection(db_path) as conn:
        created = create_memory(
            conn,
            stated("Mara lives in Oslo.", subject="Mara", keywords=["mara"]),
        )
        corrected = correct_memory(
            conn,
            created.id,
            MemoryCorrectionIn(statement="Mara lives in Bergen.", subject=None, keywords=["mara", "bergen"]),
        )

        # Content changed to the authoritative user correction...
        assert corrected.statement == "Mara lives in Bergen."
        assert corrected.subject is None
        assert corrected.keywords == ["mara", "bergen"]
        assert corrected.created_at == created.created_at  # creation time kept
        # ...and the original value is preserved in history.
        history = get_memory_history(conn, created.id)
        assert [entry.event for entry in history] == ["created", "corrected"]
        assert history[0].value["memory"]["statement"] == "Mara lives in Oslo."
        assert history[1].value["before"]["statement"] == "Mara lives in Oslo."
        assert history[1].value["after"]["statement"] == "Mara lives in Bergen."

        # History is independent of the live row: original is still recoverable
        original = history[0].value["memory"]
        assert original["id"] == created.id


def test_correction_requires_at_least_one_field(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")
    with db_connection(db_path) as conn:
        created = create_memory(conn, stated("Something to correct"))
        with pytest.raises(ValueError):
            correct_memory(conn, created.id, MemoryCorrectionIn())


def test_correction_of_missing_memory_raises_not_found(tmp_path):
    db_path = migrated_db(tmp_path)
    with db_connection(db_path) as conn:
        with pytest.raises(MemoryNotFound):
            correct_memory(conn, "no-such-id", MemoryCorrectionIn(statement="x"))
        with pytest.raises(MemoryNotFound):
            require_memory(conn, "no-such-id")


def test_correction_of_a_nonexistent_memory_writes_no_history(tmp_path):
    db_path = migrated_db(tmp_path)
    with db_connection(db_path) as conn:
        with pytest.raises(MemoryNotFound):
            correct_memory(conn, "no-such-id", MemoryCorrectionIn(statement="x"))
        count = conn.execute("SELECT COUNT(*) AS n FROM memory_history").fetchone()["n"]
        assert count == 0


# ------------------------------------------------------------- statuses -----

def test_transition_review_to_confirmed_sets_last_confirmed(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")
    with db_connection(db_path) as conn:
        created = create_memory(
            conn,
            stated("User actually confirmed this", origin="inferred"),
        )
        assert created.status == "review"
        assert created.last_confirmed_at is None

        confirmed = update_memory_status(
            conn, created.id, MemoryStatusIn(status="confirmed", reason="User confirmed in chat")
        )
        assert confirmed.status == "confirmed"
        assert confirmed.last_confirmed_at is not None
        # Timestamps use millisecond precision: equal-in-same-ms is allowed.
        assert confirmed.updated_at >= created.updated_at
        assert confirmed.updated_at == confirmed.last_confirmed_at

        history = get_memory_history(conn, created.id)
        transition = history[-1]
        assert transition.event == "corrected"  # schema CHECK limits event names
        assert transition.value["status_from"] == "review"
        assert transition.value["status_to"] == "confirmed"
        assert transition.value["reason"] == "User confirmed in chat"


def test_same_status_transition_is_noop_without_history(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")
    with db_connection(db_path) as conn:
        created = create_memory(conn, stated("Already accepted"))
        again = update_memory_status(conn, created.id, MemoryStatusIn(status="accepted"))
        assert again.status == "accepted"
        assert len(get_memory_history(conn, created.id)) == 1  # only 'created'


def test_delete_flags_status_and_keeps_row_and_history(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")
    with db_connection(db_path) as conn:
        created = create_memory(conn, stated("Precious memory"))
        deleted = update_memory_status(conn, created.id, MemoryStatusIn(status="deleted"))

        # Row retained, flagged deleted — later retrieval must exclude it.
        assert deleted.status == "deleted"
        fetched = get_memory(conn, created.id)
        assert fetched is not None and fetched.status == "deleted"
        events = [entry.event for entry in get_memory_history(conn, created.id)]
        assert events == ["created", "corrected"]


def test_status_of_missing_memory_raises_not_found(tmp_path):
    db_path = migrated_db(tmp_path)
    with db_connection(db_path) as conn:
        with pytest.raises(MemoryNotFound):
            update_memory_status(conn, "none", MemoryStatusIn(status="deleted"))


# ---------------------------------------------------- transaction safety ----

def test_failed_history_write_rolls_back_memory_insert(tmp_path, monkeypatch):
    """A memory and its history entry must succeed or fail together.

    _record_history is monkeypatched to fail after the memory INSERT, so the
    connection closes without commit and the memory row must not survive.
    """
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")

    def broken_record_history(*_args, **_kwargs):
        raise sqlite3.OperationalError("history write failed")

    monkeypatch.setattr(memory_service, "_record_history", broken_record_history)

    def attempt():
        # The failure must escape the connection context so it closes and
        # rolls back (an exception swallowed inside would trigger the commit).
        with db_connection(db_path) as conn:
            create_memory(conn, stated("Atomic?"))

    with pytest.raises(MemoryPersistenceError):
        attempt()

    monkeypatch.undo()  # restore the real history writer before continuing

    with closing(sqlite3.connect(db_path)) as raw:
        assert raw.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
        assert raw.execute("SELECT COUNT(*) FROM memory_history").fetchone()[0] == 0

    # The database still accepts a normal create afterwards.
    with db_connection(db_path) as conn:
        memory = create_memory(conn, stated("Atomic now"))
        assert memory.statement == "Atomic now"


# --------------------------------------------------------- persistence ------

def test_memories_survive_reinitialization(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")

    with db_connection(db_path) as conn:
        created = create_memory(conn, stated("Persistent memory"))
    with db_connection(db_path) as conn:  # simulated restart (new app/service)
        fetched = get_memory(conn, created.id)

    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.statement == "Persistent memory"
    assert fetched.source_message_ids == ["msg1"]


def test_memory_storage_separate_from_persona(tmp_path):
    """Persona settings must not be touched by memory operations."""
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")
    with db_connection(db_path) as conn:
        persona_before = conn.execute("SELECT * FROM persona WHERE id='default'").fetchone()
        create_memory(conn, stated("Unrelated memory"))
        persona_after = conn.execute("SELECT * FROM persona WHERE id='default'").fetchone()
        assert dict(persona_after) == dict(persona_before)


def test_history_json_values_decode_consistently(tmp_path):
    db_path = migrated_db(tmp_path)
    seed_conversation(db_path)
    seed_message(db_path, "msg1", "conv1", "seeds")
    with db_connection(db_path) as conn:
        created = create_memory(conn, stated("Decode check"))
        history = get_memory_history(conn, created.id)
    assert isinstance(history[0].value, dict)

    # Stored form is valid JSON text (read after the writer's commit).
    with closing(sqlite3.connect(db_path)) as raw:
        raw.row_factory = sqlite3.Row
        stored = raw.execute(
            "SELECT value_json FROM memory_history WHERE memory_id = ?",
            (created.id,),
        ).fetchone()
    assert stored is not None
    assert isinstance(json.loads(stored["value_json"]), dict)
