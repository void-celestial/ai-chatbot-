"""Phase 3: per-reply memory evidence trace.

Assistant replies store the memory records actually supplied to the model in
`messages.metadata_json`. The trace must match prompt evidence exactly, contain
only validated source ids, and be absent when nothing was supplied.
"""

import json
import sqlite3
from contextlib import closing

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.db.connection import db_connection
from backend.app.main import create_app
from backend.app.models.schemas import MemoryCreateIn
from backend.app.services.chat_service import insert_message
from backend.app.services.memory_service import create_memory

from .conftest import TEST_MODEL_TAG, FakeOllama, create_conversation, post_message_stream


def seed_memory(db_path, conversation_id, statement, *, message_ids=(), keywords=()):
    with db_connection(db_path) as conn:
        return create_memory(
            conn,
            MemoryCreateIn(
                kind="fact",
                statement=statement,
                keywords=list(keywords),
                origin="user_stated",
                source_conversation_id=conversation_id,
                source_message_ids=list(message_ids),
            ),
        )


def add_message(db_path, conversation_id, content):
    with db_connection(db_path) as conn:
        return insert_message(conn, conversation_id, "user", content).id


def done_event(events):
    return next(event for event in events if event["type"] == "done")


def test_assistant_reply_records_the_memory_it_used(client, settings, fake_ollama):
    source = create_conversation(client, title="Source")
    other = create_conversation(client, title="Later chat")
    source_message = add_message(
        settings.db_path, source["id"], "My sister Mara lives in Oslo."
    )
    memory = seed_memory(
        settings.db_path,
        source["id"],
        "The user's sister Mara lives in Oslo.",
        message_ids=(source_message,),
        keywords=("mara", "oslo"),
    )

    events = post_message_stream(client, other["id"], "Where does Mara live?")
    done_message = done_event(events)["message"]

    assert done_message["metadata"] == {
        "memory_evidence": [
            {
                "id": memory.id,
                "kind": "fact",
                "statement": "The user's sister Mara lives in Oslo.",
                "origin": "user_stated",
                "status": "accepted",
                "source_conversation_id": source["id"],
                "source_message_ids": [source_message],
            }
        ]
    }
    prompt = fake_ollama.last_payload["messages"][0]["content"]
    assert "Mara lives in Oslo" in prompt
    assert "memory_evidence" not in prompt

    stored = client.get(f"/api/conversations/{other['id']}/messages").json()
    user_message = next(message for message in stored if message["role"] == "user")
    assistant_message = next(message for message in stored if message["role"] == "assistant")
    assert user_message["metadata"] is None
    assert assistant_message["metadata"] == done_message["metadata"]


def test_reply_without_evidence_has_no_trace(client, settings):
    conversation = create_conversation(client)
    seed_memory(
        settings.db_path,
        conversation["id"],
        "The user's sister Mara lives in Oslo.",
        keywords=("mara",),
    )

    events = post_message_stream(client, conversation["id"], "What is the weather like?")

    assert done_event(events)["message"]["metadata"] is None


def test_memory_with_stale_source_is_not_traced(client, settings):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "My sister Mara lives in Oslo.")
    seed_memory(
        settings.db_path,
        conversation["id"],
        "The user's sister Mara lives in Oslo.",
        message_ids=(message_id,),
        keywords=("mara",),
    )
    with closing(sqlite3.connect(settings.db_path)) as raw:
        raw.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        raw.commit()

    events = post_message_stream(client, conversation["id"], "Where does Mara live?")

    assert done_event(events)["message"]["metadata"] is None


def test_trace_reflects_the_prompt_budget_only(client, settings):
    conversation = create_conversation(client)
    memory = seed_memory(
        settings.db_path,
        conversation["id"],
        "The user's sister Mara lives in Oslo.",
        keywords=("mara",),
    )
    # A one-character evidence budget cannot fit any record, so nothing is
    # supplied to the model and nothing may be reported as used.
    tiny_budget_app = create_app(
        settings=Settings(
            db_path=settings.db_path,
            model_tag=TEST_MODEL_TAG,
            memory_prompt_max_chars=1,
        ),
        ollama=FakeOllama(),
    )
    tiny_client = TestClient(tiny_budget_app, follow_redirects=False)

    events = post_message_stream(tiny_client, conversation["id"], "Where does Mara live?")

    assert memory.id  # seeded
    assert done_event(events)["message"]["metadata"] is None


def test_trace_is_persisted_not_recomputed(client, settings):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "My sister Mara lives in Oslo.")
    memory = seed_memory(
        settings.db_path,
        conversation["id"],
        "The user's sister Mara lives in Oslo.",
        message_ids=(message_id,),
        keywords=("mara",),
    )
    post_message_stream(client, conversation["id"], "Where does Mara live?")

    # Remove the source after the reply: the trace is an audit record of what
    # was supplied at that time, not a fresh validation of the memory now.
    with closing(sqlite3.connect(settings.db_path)) as raw:
        raw.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        raw.commit()

    restarted = TestClient(
        create_app(
            settings=Settings(db_path=settings.db_path, model_tag=TEST_MODEL_TAG),
            ollama=FakeOllama(),
        ),
        follow_redirects=False,
    )
    messages = restarted.get(f"/api/conversations/{conversation['id']}/messages").json()
    assistant = next(message for message in messages if message["role"] == "assistant")
    traced = assistant["metadata"]["memory_evidence"]
    assert [item["id"] for item in traced] == [memory.id]
    assert traced[0]["source_message_ids"] == [message_id]
