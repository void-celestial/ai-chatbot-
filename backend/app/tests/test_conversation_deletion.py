"""Phase 3: conversation deletion and its explicit memory consequences.

Deleting a conversation removes the conversation row and its messages but keeps
derived memories, transitioning them to `orphaned` with a history reason so
retrieval and prompt evidence exclude them. All tests use temporary databases.
"""

import sqlite3
from contextlib import closing

import pytest

import backend.app.services.chat_service as chat_service
from backend.app.db.connection import db_connection
from backend.app.models.schemas import MemoryCreateIn, MemoryStatusIn
from backend.app.services.chat_service import insert_message
from backend.app.services.memory_retrieval import retrieve_relevant_memories
from backend.app.services.memory_service import create_memory, update_memory_status

from .conftest import create_conversation, post_message_stream


def seed_memory(
    db_path,
    conversation_id: str | None,
    statement: str,
    *,
    message_ids: tuple[str, ...] = (),
    keywords: tuple[str, ...] = (),
    status: str | None = None,
):
    with db_connection(db_path) as conn:
        return create_memory(
            conn,
            MemoryCreateIn(
                kind="fact",
                statement=statement,
                keywords=list(keywords),
                origin="user_stated",
                status=status,
                source_conversation_id=conversation_id,
                source_message_ids=list(message_ids),
            ),
        )


def add_message(db_path, conversation_id: str, content: str) -> str:
    with db_connection(db_path) as conn:
        return insert_message(conn, conversation_id, "user", content).id


def history_of(client, memory_id: str) -> list[dict]:
    response = client.get(f"/api/memories/{memory_id}")
    assert response.status_code == 200, response.text
    return response.json()["history"]


def retrieve(settings, query: str):
    with db_connection(settings.db_path) as conn:
        return retrieve_relevant_memories(conn, settings, query)


def count_rows(db_path, table: str, where: str = "", params=()) -> int:
    with closing(sqlite3.connect(db_path)) as raw:
        query = f"SELECT COUNT(*) FROM {table}"
        if where:
            query += f" WHERE {where}"
        return raw.execute(query, params).fetchone()[0]


# ------------------------------------------------------------------ deletion ---

def test_delete_conversation_removes_it_and_its_messages(client, settings):
    conversation = create_conversation(client, title="Temporary")
    post_message_stream(client, conversation["id"], "Remember this?")
    assert count_rows(settings.db_path, "messages", "conversation_id = ?", (conversation["id"],)) == 2

    response = client.delete(f"/api/conversations/{conversation['id']}")

    assert response.status_code == 204
    assert response.content == b""
    assert client.get(f"/api/conversations/{conversation['id']}").status_code == 404
    assert client.get(f"/api/conversations/{conversation['id']}/messages").status_code == 404
    assert conversation["id"] not in [c["id"] for c in client.get("/api/conversations").json()]
    assert count_rows(settings.db_path, "messages", "conversation_id = ?", (conversation["id"],)) == 0
    assert count_rows(settings.db_path, "conversations", "id = ?", (conversation["id"],)) == 0


def test_derived_memories_are_orphaned_not_deleted(client, settings):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "My sister Mara lives in Oslo.")
    memory = seed_memory(
        settings.db_path,
        conversation["id"],
        "The user's sister Mara lives in Oslo.",
        message_ids=(message_id,),
        keywords=("mara", "oslo"),
    )
    assert len(retrieve(settings, "mara").memories) == 1

    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 204

    detail = client.get(f"/api/memories/{memory.id}").json()
    assert detail["statement"] == "The user's sister Mara lives in Oslo."
    assert detail["status"] == "orphaned"
    assert detail["provenance"] == {"conversation_id": None, "message_ids": [], "valid": False}
    entries = history_of(client, memory.id)
    assert [entry["event"] for entry in entries] == ["created", "corrected"]
    assert entries[-1]["value"]["status_from"] == "accepted"
    assert entries[-1]["value"]["status_to"] == "orphaned"
    assert entries[-1]["value"]["reason"] == "Source conversation deleted"
    assert retrieve(settings, "mara").memories == []


def test_orphaned_memory_is_excluded_from_like_fallback_too(client, settings):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "My sister Mara lives in Oslo.")
    seed_memory(
        settings.db_path,
        conversation["id"],
        "The user's sister Mara lives in Oslo.",
        message_ids=(message_id,),
        keywords=("mara",),
    )

    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 204
    # Drop the FTS index after the lifecycle writes so the fallback scan is the
    # only retrieval path under test.
    with closing(sqlite3.connect(settings.db_path)) as raw:
        raw.execute("DROP TABLE memories_fts")
        raw.commit()

    result = retrieve(settings, "mara")
    assert result.error is None
    assert result.memories == []


def test_deletion_leaves_other_conversations_and_memories_intact(client, settings):
    first = create_conversation(client, title="First")
    second = create_conversation(client, title="Second")
    first_message = add_message(settings.db_path, first["id"], "I live in Oslo.")
    second_message = add_message(settings.db_path, second["id"], "I live in Bergen.")
    first_memory = seed_memory(
        settings.db_path, first["id"], "The user lives in Oslo.", message_ids=(first_message,)
    )
    second_memory = seed_memory(
        settings.db_path,
        second["id"],
        "The user lives in Bergen.",
        message_ids=(second_message,),
        keywords=("bergen",),
    )

    assert client.delete(f"/api/conversations/{first['id']}").status_code == 204

    assert client.get(f"/api/conversations/{second['id']}").status_code == 200
    assert len(client.get(f"/api/conversations/{second['id']}/messages").json()) == 1
    assert client.get(f"/api/memories/{first_memory.id}").json()["status"] == "orphaned"
    assert client.get(f"/api/memories/{second_memory.id}").json()["status"] == "accepted"
    assert [item.id for item in retrieve(settings, "bergen").memories] == [second_memory.id]


def test_deleted_and_superseded_statuses_are_not_overwritten(client, settings):
    conversation = create_conversation(client)
    deleted_memory = seed_memory(settings.db_path, conversation["id"], "The user lives in Oslo.")
    superseded_memory = seed_memory(settings.db_path, conversation["id"], "The user lives in Bergen.")
    with db_connection(settings.db_path) as conn:
        update_memory_status(conn, deleted_memory.id, MemoryStatusIn(status="deleted"))
        update_memory_status(conn, superseded_memory.id, MemoryStatusIn(status="superseded"))

    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 204

    assert client.get(f"/api/memories/{deleted_memory.id}").json()["status"] == "deleted"
    assert client.get(f"/api/memories/{superseded_memory.id}").json()["status"] == "superseded"


def test_message_only_memory_is_orphaned_when_its_message_is_deleted(client, settings):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "My sister Mara lives in Oslo.")
    memory = seed_memory(
        settings.db_path,
        None,
        "The user's sister Mara lives in Oslo.",
        message_ids=(message_id,),
        keywords=("mara",),
    )
    assert len(retrieve(settings, "mara").memories) == 1

    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 204

    assert client.get(f"/api/memories/{memory.id}").json()["status"] == "orphaned"
    assert retrieve(settings, "mara").memories == []


def test_missing_conversation_returns_404_and_repeat_delete_is_404(client):
    assert client.delete("/api/conversations/does-not-exist").status_code == 404


def test_repeat_delete_after_success_returns_404(client):
    conversation = create_conversation(client)
    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 204
    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 404


def test_chat_to_deleted_conversation_returns_404(client):
    conversation = create_conversation(client)
    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 204

    response = client.post(
        f"/api/conversations/{conversation['id']}/messages", json={"content": "Hello?"}
    )
    assert response.status_code == 404


def test_deletion_failure_rolls_back_the_conversation(client, settings, monkeypatch):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "I live in Oslo.")
    memory = seed_memory(
        settings.db_path, conversation["id"], "The user lives in Oslo.", message_ids=(message_id,)
    )

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(chat_service, "orphan_memories_for_conversation", fail)

    response = client.delete(f"/api/conversations/{conversation['id']}")

    assert response.status_code == 500
    assert "Could not delete the conversation." in response.json()["detail"]
    monkeypatch.undo()
    assert client.get(f"/api/conversations/{conversation['id']}").status_code == 200
    assert client.get(f"/api/memories/{memory.id}").json()["status"] == "accepted"
