"""Phase 2, Step 4 tests: memory extraction and validation pipeline.

Covers: provenance derived from real records (never trusted from the model),
user-stated promotion rules, assistant-authored downgrade, quote anchoring,
contradiction/duplicate/conflict/sensitive/fictional skips, malformed output,
Ollama failure, batch limits, and chat-path isolation (extraction failure
never breaks streaming or persistence). All model responses are fakes; no
network access is required.
"""

import asyncio
import sqlite3
from contextlib import closing

import httpx
import pytest

from backend.app.db.connection import db_connection, init_db
from backend.app.db.migration import migrate
from backend.app.models.schemas import MemoryCreateIn
from backend.app.services.chat_service import insert_message
from backend.app.services.memory_extraction import extract_memories_from_exchange
from backend.app.services.memory_service import create_memory

from .conftest import create_conversation, post_message_stream

# The test model tag comes from conftest's settings fixture (TEST_MODEL_TAG).


def make_db(tmp_path) -> object:
    db_path = tmp_path / "extraction.db"
    init_db(db_path)
    migrate(db_path, backup=False)
    return db_path


def add_conversation(db_path, conversation_id: str = "conv1") -> str:
    with db_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, status, created_at, updated_at)"
            " VALUES (?, 'Chat', 'active', 't0', 't0')",
            (conversation_id,),
        )
    return conversation_id


def add_message(db_path, conversation_id: str, role: str, content: str) -> str:
    with db_connection(db_path) as conn:
        return insert_message(conn, conversation_id, role, content).id


def add_exchange(db_path, conversation_id: str, user_content: str, assistant_content: str | None = None):
    """Seed a real exchange; return the actual message ids (user, assistant|None)."""
    user_id = add_message(db_path, conversation_id, "user", user_content)
    assistant_id = None
    if assistant_content is not None:
        assistant_id = add_message(db_path, conversation_id, "assistant", assistant_content)
    return user_id, assistant_id


def run_extraction(db_path, fake_ollama, settings, conversation_id: str = "conv1"):
    with db_connection(db_path) as conn:
        return asyncio.run(extract_memories_from_exchange(conn, fake_ollama, settings, conversation_id))


def memory_rows(db_path) -> list[sqlite3.Row]:
    with closing(sqlite3.connect(db_path)) as raw:
        raw.row_factory = sqlite3.Row
        return raw.execute("SELECT * FROM memories ORDER BY created_at").fetchall()


def candidate(statement: str, quote: str, message_index: int = 1, **extra) -> dict:
    base = {
        "kind": "fact",
        "statement": statement,
        "subject": "Mara",
        "keywords": ["mara"],
        "origin": "user_stated",
        "quote": quote,
        "message_index": message_index,
        "fictional": False,
        "sensitive": False,
    }
    return {**base, **extra}


USER_TEXT = "Hey! Did you know my sister Mara lives in Oslo? Pretty cool."
ASSISTANT_TEXT = "That sounds lovely, Oslo is a beautiful city."
MEMORY_OUT_OF_USER_STATEMENT = "The user's sister Mara lives in Oslo."


# ------------------------------------------------------- explicit statements ---

def test_explicit_user_statement_saved_as_user_stated(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    user_id, _ = add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(MEMORY_OUT_OF_USER_STATEMENT, quote="my sister Mara lives in Oslo")
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.error is None
    assert len(result.saved) == 1
    memory = result.saved[0]
    assert memory.origin == "user_stated"
    assert memory.status == "accepted"
    assert memory.confidence == 1.0
    assert memory.kind == "fact"
    assert memory.statement == MEMORY_OUT_OF_USER_STATEMENT
    assert memory.subject == "Mara"
    # Provenance is derived from the real records, exactly.
    assert memory.source_conversation_id == "conv1"
    assert memory.source_message_ids == [user_id]


def test_inference_is_not_promoted_to_user_stated(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(
            "The user might enjoy hiking.",
            quote="sounds lovely",
            origin="inferred",
            subject=None,
            kind="preference",
        )
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert len(result.saved) == 1
    memory = result.saved[0]
    assert memory.origin == "inferred"
    assert memory.status == "review"
    assert memory.confidence == 0.5


def test_assistant_authenticated_claim_cannot_become_user_stated(tmp_path, fake_ollama, settings):
    """Model claims user_stated but only supports it with the assistant message."""
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    user_id, assistant_id = add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(
            "Oslo is a beautiful city.",
            quote="beautiful city",
            message_index=2,  # the assistant message
        ),
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert len(result.saved) == 1
    memory = result.saved[0]
    assert memory.origin == "inferred"
    assert memory.status == "review"
    assert memory.confidence == 0.5
    assert memory.source_message_ids == [assistant_id]
    assert memory.source_message_ids != [user_id]


def test_exchange_without_any_user_message_is_skipped(tmp_path, fake_ollama, settings):
    """An exchange always starts at a user message; assistant-only history does
    not produce an exchange to analyse."""
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_message(db_path, "conv1", "assistant", ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate("The user lives in Oslo.", quote="lives in Oslo", message_index=1)
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.error == "no_exchange"
    assert fake_ollama.chat_json_calls == []
    assert memory_rows(db_path) == []


# ------------------------------------------------------- derived provenance ----

def test_source_ids_derived_from_real_records_and_model_ids_ignored(tmp_path, fake_ollama, settings):
    """The model cannot name sources: any self-supplied id fields are ignored."""
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    user_id, _ = add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(
            MEMORY_OUT_OF_USER_STATEMENT,
            quote="Mara lives in Oslo",
            source_message_ids=["forged-id"],          # must be ignored
            source_conversation_id="forged-conv",      # must be ignored
        )
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    memory = result.saved[0]
    assert memory.source_message_ids == [user_id]
    assert memory.source_conversation_id == "conv1"


def test_out_of_range_message_index_falls_back_to_content_derivation(tmp_path, fake_ollama, settings):
    """The index is only a hint: provenance is derived from the real records.
    A wrong index does not block a genuinely user-anchored quote."""
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    user_id, _ = add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(MEMORY_OUT_OF_USER_STATEMENT, quote="Mara lives in Oslo", message_index=99)
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.error is None
    assert len(result.saved) == 1
    assert result.saved[0].source_message_ids == [user_id]
    assert result.saved[0].origin == "user_stated"


@pytest.mark.parametrize("index", [0, -2])
def test_invalid_message_index_is_rejected_by_structure(tmp_path, fake_ollama, settings, index):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(MEMORY_OUT_OF_USER_STATEMENT, quote="Mara lives in Oslo", message_index=index)
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.saved == []
    assert result.skipped[0].reason == "invalid_structure"


def test_quote_must_anchor_to_a_real_message(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(
            "Supports a claim nobody made.",
            quote="the user enjoys theoretical physics in Oslo",  # not in any message
        )
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.saved == []
    assert result.skipped[0].reason == "unsupported_quote"


def test_quote_matching_is_whitespace_and_case_tolerant(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(
            MEMORY_OUT_OF_USER_STATEMENT,
            quote="  My  sister   MARA\n lives  in  Oslo  ",  # different case/whitespace
        )
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert len(result.saved) == 1
    assert result.saved[0].origin == "user_stated"


def test_contradiction_with_own_source_is_skipped(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(
            "Mara does not live in Oslo.",  # negates the quoted content
            quote="Mara lives in Oslo",
        )
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.saved == []
    assert result.skipped[0].reason == "contradicts_source"


def test_negation_free_paraphrase_is_not_flagged_as_contradiction(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate("Mara lives in Oslo.", quote="Mara lives in Oslo")
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert len(result.saved) == 1
    assert all(r.reason != "contradicts_source" for r in result.skipped)


# ------------------------------------------------------- structural rejection ---

def test_malformed_model_output_reports_error_and_saves_nothing(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = "this is definitely not {{ valid json"

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.error == "malformed_output"
    assert result.saved == []
    assert memory_rows(db_path) == []


def test_malformed_candidates_are_skipped_good_ones_survive(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        "not an object",
        {"kind": "fact", "statement": "", "quote": "x", "message_index": 1},            # empty statement
        {"kind": "story_fact", "statement": "x", "quote": "Oslo", "message_index": 1},  # bad kind
        {"kind": "fact", "statement": "x" * 2001, "quote": "Oslo", "message_index": 1},  # over limit
        {"kind": "fact", "statement": "ok", "quote": None, "message_index": 1},         # no quote
        candidate(MEMORY_OUT_OF_USER_STATEMENT, quote="Mara lives in Oslo"),
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.error is None
    assert len(result.saved) == 1
    reasons = [record.reason for record in result.skipped]
    assert reasons.count("invalid_structure") == 5


def test_batch_limit_is_enforced(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    filler = candidate("Filler statement number one.", quote="Oslo", subject=None)
    fake_ollama.extraction_json = {"candidates": [
        candidate(MEMORY_OUT_OF_USER_STATEMENT, quote="Mara lives in Oslo")
    ] + [dict(filler, statement=f"Filler statement {n}.") for n in range(14)]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert len(result.saved) <= settings.max_extraction_candidates
    assert any(r.reason == "batch_limit_exceeded" for r in result.skipped)


# ------------------------------------------------------------ duplicates -------

def test_duplicate_of_existing_memory_is_skipped(tmp_path, fake_ollama, settings):
    """Documented policy: exact (normalized) duplicates against live memories
    are skipped, not overwritten and not re-saved."""
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    user_id, _ = add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    with db_connection(db_path) as conn:
        create_memory(
            conn,
            MemoryCreateIn(
                kind="fact",
                statement="Mara lives in Oslo.",
                subject="Mara",
                origin="user_stated",
                source_conversation_id="conv1",
                source_message_ids=[user_id],
            ),
        )
    fake_ollama.extraction_json = {"candidates": [
        # Same normalized statement (case/whitespace differ only).
        candidate("MARA   lives  in oslo.", quote="Mara lives in Oslo")
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.saved == []
    assert result.skipped[0].reason == "duplicate"
    assert len(memory_rows(db_path)) == 1


def test_duplicates_within_one_batch_are_collapsed(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(MEMORY_OUT_OF_USER_STATEMENT, quote="Mara lives in Oslo"),
        candidate(MEMORY_OUT_OF_USER_STATEMENT, quote="Mara lives in Oslo"),
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert len(result.saved) == 1
    assert result.skipped[0].reason == "duplicate"


# ------------------------------------------------------------ conflicts --------

def test_conflicting_candidate_is_skipped_and_existing_untouched(tmp_path, fake_ollama, settings):
    """Where semantic conflict detection is unavailable, ambiguous conflicts are
    skipped — never silently overwritten."""
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    user_id, _ = add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    with db_connection(db_path) as conn:
        existing = create_memory(
            conn,
            MemoryCreateIn(
                kind="fact",
                statement="Mara lives in Oslo.",
                subject="Mara",
                origin="user_stated",
                source_conversation_id="conv1",
                source_message_ids=[user_id],
            ),
        )
    fake_ollama.extraction_json = {"candidates": [
        candidate(
            "The user's sister Mara strongly dislikes Oslo.",
            quote="my sister Mara",  # anchored, but conflicts on the same subject
        )
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.saved == []
    assert result.skipped[0].reason == "potential_conflict"
    rows = memory_rows(db_path)
    assert len(rows) == 1
    assert rows[0]["id"] == existing.id
    assert rows[0]["statement"] == "Mara lives in Oslo."


# ------------------------------------------------- sensitive and fictional -----

def test_model_flagged_sensitive_candidate_is_skipped(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(
        db_path, "conv1",
        "I should mention I was recently diagnosed with narcolepsy.",
        "I'm sorry to hear that.",
    )
    fake_ollama.extraction_json = {"candidates": [
        candidate(
            "The user was diagnosed with narcolepsy.",
            quote="diagnosed with narcolepsy",
            sensitive=True,
            subject=None,
        )
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.saved == []
    assert result.skipped[0].reason == "sensitive_content"


def test_deterministic_sensitive_filter_catches_unflagged_content(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(
            "The user's salary at the new job is high.",
            quote="Mara lives in Oslo",  # quote is real; the statement is sensitive
            subject="salary",
        )
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.saved == []
    assert result.skipped[0].reason == "sensitive_content"


def test_fictional_candidates_are_skipped(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(
        db_path, "conv1",
        "In my story, my character Elara rules the Ashen Kingdom.",
        "Nice worldbuilding!",
    )
    fake_ollama.extraction_json = {"candidates": [
        candidate(
            "Elara rules the Ashen Kingdom.",
            quote="Elara rules the Ashen Kingdom",
            fictional=True,
            subject="Elara",
        )
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.saved == []
    assert result.skipped[0].reason == "fictional_content"


def test_story_kind_is_rejected_by_schema(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        {"kind": "story_fact", "statement": "x", "origin": "user_stated",
         "quote": "Mara lives in Oslo", "message_index": 1}
    ]}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.saved == []
    assert result.skipped[0].reason == "invalid_structure"


# ------------------------------------------------------- Ollama failures -------

@pytest.mark.parametrize("behavior, expected_error", [
    ("fail", "model_error:generation_failed"),
    ("unavailable", "model_error:ollama_unavailable"),
])
def test_model_failures_are_reported_without_memories(
    tmp_path, fake_ollama, settings, behavior, expected_error
):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extract_behavior = behavior

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.error == expected_error
    assert result.saved == []
    assert memory_rows(db_path) == []


def test_empty_candidates_result_is_clean(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": []}

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.error is None
    assert result.candidates_seen == 0
    assert result.saved == []


def test_missing_exchange_is_reported(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)

    result = run_extraction(db_path, fake_ollama, settings)

    assert result.error == "no_exchange"
    assert fake_ollama.chat_json_calls == []


def test_missing_conversation_is_reported(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)

    result = run_extraction(db_path, fake_ollama, settings, conversation_id="ghost")

    assert result.error == "conversation_not_found"
    assert fake_ollama.chat_json_calls == []


def test_extraction_makes_no_direct_http_requests(tmp_path, fake_ollama, settings, monkeypatch):
    """Extraction must only ever talk to the injected local client."""
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    fake_ollama.extraction_json = {"candidates": [
        candidate(MEMORY_OUT_OF_USER_STATEMENT, quote="Mara lives in Oslo")
    ]}

    def no_http(*_args, **_kwargs):
        raise AssertionError("Memory extraction must not create HTTP clients directly")

    monkeypatch.setattr(httpx, "AsyncClient", no_http)

    result = run_extraction(db_path, fake_ollama, settings)

    assert len(result.saved) == 1


def test_extraction_does_not_touch_persona(tmp_path, fake_ollama, settings):
    db_path = make_db(tmp_path)
    add_conversation(db_path)
    add_exchange(db_path, "conv1", USER_TEXT, ASSISTANT_TEXT)
    with db_connection(db_path) as conn:
        persona_before = conn.execute("SELECT * FROM persona WHERE id='default'").fetchone()
    fake_ollama.extraction_json = {"candidates": [
        candidate(MEMORY_OUT_OF_USER_STATEMENT, quote="Mara lives in Oslo")
    ]}

    run_extraction(db_path, fake_ollama, settings)

    with db_connection(db_path) as conn:
        persona_after = conn.execute("SELECT * FROM persona WHERE id='default'").fetchone()
    assert dict(persona_after) == dict(persona_before)


# --------------------------------------------------- chat-path integration -----

def test_chat_streams_and_extracts_in_background(client, fake_ollama, settings):
    """End-to-end: chat completes normally and the background task persists a
    validated memory afterwards."""
    conversation = create_conversation(client)
    fake_ollama.extraction_json = {"candidates": [
        candidate(MEMORY_OUT_OF_USER_STATEMENT, quote="my sister Mara lives in Oslo")
    ]}

    events = post_message_stream(client, conversation["id"], USER_TEXT)
    types = [event["type"] for event in events]

    assert types[0] == "user_message"
    assert "assistant_started" in types
    assert "delta" in types
    assert types[-1] == "done"

    rows = memory_rows(settings.db_path)
    assert len(rows) == 1
    stored = rows[0]
    assert stored["origin"] == "user_stated"
    assert stored["status"] == "accepted"
    assert stored["source_conversation_id"] == conversation["id"]
    with closing(sqlite3.connect(settings.db_path)) as raw:
        raw.row_factory = sqlite3.Row
        user_msg = raw.execute(
            "SELECT id FROM messages WHERE conversation_id = ? AND role='user'",
            (conversation["id"],),
        ).fetchone()
    assert stored["source_message_ids_json"] == f'["{user_msg["id"]}"]'


def test_extraction_failure_does_not_break_chat_streaming_or_persistence(
    client, fake_ollama, settings
):
    """Ollama unavailable *for extraction* must leave normal chat untouched."""
    conversation = create_conversation(client)
    fake_ollama.extract_behavior = "unavailable"

    events = post_message_stream(client, conversation["id"], USER_TEXT)
    types = [event["type"] for event in events]

    assert types[0] == "user_message"
    assert "assistant_started" in types
    assert "delta" in types
    assert types[-1] == "done"
    with closing(sqlite3.connect(settings.db_path)) as raw:
        raw.row_factory = sqlite3.Row
        assistant = raw.execute(
            "SELECT generation_status FROM messages WHERE conversation_id = ? AND role='assistant'",
            (conversation["id"],),
        ).fetchone()
        memory_count = raw.execute("SELECT COUNT(*) AS n FROM memories").fetchone()
    assert assistant is not None and assistant["generation_status"] == "complete"
    assert memory_count["n"] == 0


def test_malformed_extraction_output_does_not_break_chat(client, fake_ollama, settings):
    conversation = create_conversation(client)
    fake_ollama.extraction_json = "<<malformed>>"

    events = post_message_stream(client, conversation["id"], "Hello there, friend!")
    types = [event["type"] for event in events]

    assert "assistant_started" in types
    assert "delta" in types
    assert types[-1] == "done"
    assert len(memory_rows(settings.db_path)) == 0


def test_extraction_prompt_covers_only_the_last_exchange(client, fake_ollama, settings):
    """The extraction request covers the last exchange only, never the archive."""
    conversation = create_conversation(client)
    fake_ollama.extraction_json = {"candidates": []}

    post_message_stream(client, conversation["id"], "A quick note about this conversation.")

    assert len(fake_ollama.chat_json_calls) == 1
    request = fake_ollama.last_extract_request
    assert request["model"] == settings.model_tag
    data_message = request["messages"][-1]
    assert data_message["role"] == "user"
    assert "[Message 1 (user)]" in data_message["content"]
    assert "A quick note about this conversation." in data_message["content"]


def test_repeated_extraction_of_same_exchange_does_not_duplicate(client, fake_ollama, settings):
    conversation = create_conversation(client)
    fake_ollama.extraction_json = {"candidates": [
        candidate(MEMORY_OUT_OF_USER_STATEMENT, quote="my sister Mara lives in Oslo")
    ]}

    post_message_stream(client, conversation["id"], USER_TEXT)
    first_rows = memory_rows(settings.db_path)
    assert len(first_rows) == 1

    with db_connection(settings.db_path) as conn:
        asyncio.run(
            extract_memories_from_exchange(conn, fake_ollama, settings, conversation["id"])
        )

    rows = memory_rows(settings.db_path)
    assert len(rows) == 1  # duplicate skipped, existing row untouched
    assert rows[0]["id"] == first_rows[0]["id"]
