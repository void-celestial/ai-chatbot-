"""Phase 3: local data export.

The export is a read-only JSON snapshot of the documented scope. Tests cover
shape/counts, included entities, inactive and orphaned memories, deletion
consequences, and the fact that the export does not mutate the archive.
"""

from backend.app.db.connection import db_connection
from backend.app.models.schemas import MemoryCreateIn
from backend.app.services.chat_service import insert_message
from backend.app.services.memory_service import create_memory

from .conftest import create_conversation, post_message_stream


def seed_memory(db_path, conversation_id, statement, *, message_ids=(), keywords=(), status=None):
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


def add_message(db_path, conversation_id, content):
    with db_connection(db_path) as conn:
        return insert_message(conn, conversation_id, "user", content).id


def test_export_has_documented_shape_and_scope(client):
    response = client.get("/api/export")

    assert response.status_code == 200
    body = response.json()
    assert body["format"] == "companion-export"
    assert body["format_version"] == 1
    assert body["exported_at"].endswith("Z")
    assert body["counts"] == {
        "conversations": 0,
        "messages": 0,
        "memories": 0,
        "memory_history": 0,
    }
    assert body["conversations"] == []
    assert body["messages"] == []
    assert body["memories"] == []
    assert body["memory_history"] == []
    assert body["persona"]["name"] == "Companion"
    assert any("FTS5" in item for item in body["scope"]["excludes"])
    assert any("backup" in item for item in body["scope"]["excludes"])
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"


def test_export_includes_conversations_messages_memories_history_and_persona(client, settings):
    assert client.patch("/api/persona", json={"name": "Nova"}).status_code == 200
    conversation = create_conversation(client, title="Export me")
    message_id = add_message(settings.db_path, conversation["id"], "My sister Mara lives in Oslo.")
    memory = seed_memory(
        settings.db_path,
        conversation["id"],
        "The user's sister Mara lives in Oslo.",
        message_ids=(message_id,),
        keywords=("mara", "oslo"),
    )

    body = client.get("/api/export").json()

    assert body["persona"]["name"] == "Nova"
    assert body["counts"] == {
        "conversations": 1,
        "messages": 1,
        "memories": 1,
        "memory_history": 1,
    }
    assert body["conversations"][0]["id"] == conversation["id"]
    assert body["conversations"][0]["title"] == "Export me"
    assert body["messages"][0]["id"] == message_id
    assert body["messages"][0]["content"] == "My sister Mara lives in Oslo."
    exported_memory = body["memories"][0]
    assert exported_memory["id"] == memory.id
    assert exported_memory["statement"] == "The user's sister Mara lives in Oslo."
    assert exported_memory["keywords"] == ["mara", "oslo"]
    assert exported_memory["source_conversation_id"] == conversation["id"]
    assert exported_memory["source_message_ids"] == [message_id]
    assert body["memory_history"][0]["event"] == "created"
    assert body["memory_history"][0]["memory_id"] == memory.id


def test_export_includes_inactive_memories(client, settings):
    conversation = create_conversation(client)
    memory = seed_memory(settings.db_path, conversation["id"], "The user lives in Oslo.")
    assert client.delete(f"/api/memories/{memory.id}").status_code == 204

    body = client.get("/api/export").json()

    assert body["memories"][0]["status"] == "deleted"
    assert body["counts"]["memories"] == 1
    assert len(body["memory_history"]) == 2


def test_export_reflects_conversation_deletion(client, settings):
    conversation = create_conversation(client, title="Gone soon")
    message_id = add_message(settings.db_path, conversation["id"], "My sister Mara lives in Oslo.")
    memory = seed_memory(
        settings.db_path,
        conversation["id"],
        "The user's sister Mara lives in Oslo.",
        message_ids=(message_id,),
        keywords=("mara",),
    )

    assert client.delete(f"/api/conversations/{conversation['id']}").status_code == 204
    body = client.get("/api/export").json()

    assert body["conversations"] == []
    assert body["messages"] == []
    assert body["counts"]["memories"] == 1
    exported = body["memories"][0]
    assert exported["id"] == memory.id
    assert exported["status"] == "orphaned"
    history = [entry for entry in body["memory_history"] if entry["memory_id"] == memory.id]
    assert history[-1]["value"]["reason"] == "Source conversation deleted"
    # The raw stored references remain in the export as audit data; they are
    # not presented as verified sources.
    assert exported["source_message_ids"] == [message_id]


def test_export_does_not_mutate_the_archive(client, settings):
    conversation = create_conversation(client)
    post_message_stream(client, conversation["id"], "Hello there")

    first = client.get("/api/export").json()
    second = client.get("/api/export").json()

    assert first["counts"] == second["counts"]
    assert [m["id"] for m in first["messages"]] == [m["id"] for m in second["messages"]]
    assert len(client.get(f"/api/conversations/{conversation['id']}/messages").json()) == 2
