"""Phase 2, Step 6 tests: memory management API.

Exercises the real FastAPI app against temporary SQLite databases: listing,
filtering, pagination, provenance/history exposure, confirm/reject lifecycle,
corrections with audit history, soft deletion (including retrieval exclusion
through both FTS5 and the LIKE fallback), request-forgery rejection, and safe
error handling. No network and no real model are used.
"""

import json
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

import backend.app.services.memory_service as memory_service
from backend.app.config import MAX_MEMORY_STATEMENT_CHARS, Settings
from backend.app.db.connection import db_connection
from backend.app.main import create_app
from backend.app.models.schemas import MemoryCreateIn, MemoryStatusIn
from backend.app.services.chat_service import insert_message
from backend.app.services.memory_retrieval import retrieve_relevant_memories
from backend.app.services.memory_service import (
    MemoryPersistenceError,
    create_memory,
    update_memory_status,
)

from .conftest import TEST_MODEL_TAG, FakeOllama, create_conversation, post_message_stream

MISSING_ID = "0" * 32


# ------------------------------------------------------------------ helpers ---

def seed(
    db_path,
    conversation_id: str,
    statement: str,
    *,
    kind: str = "fact",
    origin: str = "user_stated",
    status: str | None = None,
    subject: str | None = None,
    keywords: tuple[str, ...] = (),
    message_ids: tuple[str, ...] = (),
):
    """Create a memory through the real service so provenance is valid."""
    with db_connection(db_path) as conn:
        return create_memory(
            conn,
            MemoryCreateIn(
                kind=kind,
                statement=statement,
                subject=subject,
                keywords=list(keywords),
                origin=origin,
                status=status,
                source_conversation_id=conversation_id,
                source_message_ids=list(message_ids),
            ),
        )


def review_memory(db_path, conversation_id: str, statement: str, **kwargs):
    """Inferred memories default to `review` (awaiting confirmation)."""
    return seed(
        db_path,
        conversation_id,
        statement,
        origin="inferred",
        keywords=kwargs.pop("keywords", ("oslo",)),
        **kwargs,
    )


def add_message(db_path, conversation_id: str, content: str) -> str:
    with db_connection(db_path) as conn:
        return insert_message(conn, conversation_id, "user", content).id


def list_ids(client, query: str = "") -> list[str]:
    response = client.get(f"/api/memories{query}")
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def history_of(client, memory_id: str) -> list[dict]:
    response = client.get(f"/api/memories/{memory_id}")
    assert response.status_code == 200, response.text
    return response.json()["history"]


def retrieve(settings, query: str):
    with db_connection(settings.db_path) as conn:
        return retrieve_relevant_memories(conn, settings, query)


def drop_fts_index(db_path) -> None:
    with closing(sqlite3.connect(db_path)) as raw:
        raw.execute("DROP TABLE memories_fts")
        raw.commit()


# ------------------------------------------------------------------ listing ---

def test_list_returns_default_page_shape(client, settings):
    conversation = create_conversation(client)
    first = seed(settings.db_path, conversation["id"], "The user likes tea.")
    second = seed(
        settings.db_path,
        conversation["id"],
        "The user is learning Norwegian.",
        kind="goal",
        origin="inferred",
    )

    response = client.get("/api/memories")
    body = response.json()

    assert response.status_code == 200
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert {item["id"] for item in body["items"]} == {first.id, second.id}
    item = body["items"][0]
    assert set(item) == {
        "id", "kind", "statement", "subject", "keywords", "origin", "status",
        "confidence", "provenance", "created_at", "updated_at", "last_confirmed_at",
    }
    # Raw stored source columns are not part of the API contract.
    assert "source_conversation_id" not in item
    assert "source_message_ids" not in item
    assert item["provenance"]["conversation_id"] == conversation["id"]
    assert item["provenance"]["valid"] is True


@pytest.mark.parametrize(
    "query,expected_count",
    [
        ("?status=review", 1),
        ("?status=accepted", 1),
        ("?kind=preference", 1),
        ("?origin=inferred", 1),
        ("?origin=user_stated", 1),
        ("?status=review&origin=inferred", 1),
        ("?status=deleted", 0),
        ("?kind=event", 0),
    ],
)
def test_list_filters(client, settings, query, expected_count):
    conversation = create_conversation(client)
    seed(
        settings.db_path,
        conversation["id"],
        "The user prefers green tea.",
        kind="preference",
        origin="inferred",
    )
    seed(settings.db_path, conversation["id"], "The user lives in Oslo.")

    response = client.get(f"/api/memories{query}")
    body = response.json()

    assert response.status_code == 200
    assert body["total"] == expected_count
    assert len(body["items"]) == expected_count


def test_invalid_filter_values_are_rejected(client):
    for query in ("?status=archived", "?kind=story", "?origin=guessed"):
        assert client.get(f"/api/memories{query}").status_code == 422


def test_pagination_bounds_and_total(client, settings):
    conversation = create_conversation(client)
    created = [
        seed(settings.db_path, conversation["id"], f"Memory number {index}.")
        for index in range(3)
    ]

    first_page = client.get("/api/memories?limit=2&offset=0").json()
    second_page = client.get("/api/memories?limit=2&offset=2").json()

    assert first_page["total"] == 3
    assert len(first_page["items"]) == 2
    assert len(second_page["items"]) == 1
    combined = [item["id"] for item in first_page["items"] + second_page["items"]]
    assert sorted(combined) == sorted(memory.id for memory in created)
    assert len(set(combined)) == 3

    for query in ("?limit=0", "?limit=101", "?offset=-1"):
        assert client.get(f"/api/memories{query}").status_code == 422

    beyond = client.get("/api/memories?offset=99").json()
    assert beyond["items"] == []
    assert beyond["total"] == 3


def test_list_order_is_deterministic(client, settings):
    conversation = create_conversation(client)
    for index in range(3):
        seed(settings.db_path, conversation["id"], f"Memory {index}.")

    assert list_ids(client) == list_ids(client)


# ------------------------------------------------------------------- detail ---

def test_detail_exposes_memory_provenance_and_history(client, settings):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "My sister Mara lives in Oslo.")
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user's sister Mara lives in Oslo.",
        subject="Mara",
        keywords=("mara", "oslo"),
        message_ids=(message_id,),
    )

    response = client.get(f"/api/memories/{memory.id}")
    body = response.json()

    assert response.status_code == 200
    assert body["id"] == memory.id
    assert body["statement"] == memory.statement
    assert body["origin"] == "user_stated"
    assert body["status"] == "accepted"
    assert body["provenance"] == {
        "conversation_id": conversation["id"],
        "message_ids": [message_id],
        "valid": True,
    }
    assert [entry["event"] for entry in body["history"]] == ["created"]


@pytest.mark.parametrize("memory_id", ["not-a-memory-id", "0" * 31, "Z" * 32])
def test_malformed_memory_ids_are_rejected(client, memory_id):
    assert client.get(f"/api/memories/{memory_id}").status_code == 422
    assert client.patch(f"/api/memories/{memory_id}", json={"statement": "x"}).status_code == 422
    assert client.delete(f"/api/memories/{memory_id}").status_code == 422


def test_missing_memory_returns_404(client):
    assert client.get(f"/api/memories/{MISSING_ID}").status_code == 404
    assert client.patch(f"/api/memories/{MISSING_ID}", json={"statement": "x"}).status_code == 404
    assert client.post(f"/api/memories/{MISSING_ID}/confirm").status_code == 404
    assert client.post(f"/api/memories/{MISSING_ID}/reject").status_code == 404
    assert client.delete(f"/api/memories/{MISSING_ID}").status_code == 404


def test_stale_message_reference_is_not_exposed(client, settings):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "Temporary note.")
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user wrote a temporary note.",
        message_ids=(message_id,),
    )
    with closing(sqlite3.connect(settings.db_path)) as raw:
        raw.execute("DELETE FROM messages WHERE id = ?", (message_id,))
        raw.commit()

    provenance = client.get(f"/api/memories/{memory.id}").json()["provenance"]

    # The conversation survives, but the declared message-level evidence is
    # gone. Message-bearing provenance is therefore invalid.
    assert provenance == {
        "conversation_id": conversation["id"],
        "message_ids": [],
        "valid": False,
    }


def test_partially_stale_message_references_invalidate_whole_memory(client, settings):
    conversation = create_conversation(client)
    stale_id = add_message(settings.db_path, conversation["id"], "First note about Mara.")
    surviving_id = add_message(settings.db_path, conversation["id"], "Second note about Mara.")
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user's sister Mara lives in Oslo.",
        keywords=("mara",),
        message_ids=(stale_id, surviving_id),
    )
    with closing(sqlite3.connect(settings.db_path)) as raw:
        raw.execute("DELETE FROM messages WHERE id = ?", (stale_id,))
        raw.commit()

    provenance = client.get(f"/api/memories/{memory.id}").json()["provenance"]

    # All-or-nothing: the surviving id is still shown for audit, but the
    # memory is invalid and cannot be retrieved as grounded evidence.
    assert provenance == {
        "conversation_id": conversation["id"],
        "message_ids": [surviving_id],
        "valid": False,
    }
    assert retrieve(settings, "mara").memories == []


def test_message_from_a_different_conversation_is_invalid(client, settings):
    conversation = create_conversation(client)
    other = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "Only in the first chat.")
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user mentioned the first chat.",
        message_ids=(message_id,),
    )
    with closing(sqlite3.connect(settings.db_path)) as raw:
        raw.execute(
            "UPDATE memories SET source_conversation_id = ? WHERE id = ?",
            (other["id"], memory.id),
        )
        raw.commit()

    provenance = client.get(f"/api/memories/{memory.id}").json()["provenance"]

    assert provenance == {
        "conversation_id": other["id"],
        "message_ids": [],
        "valid": False,
    }


def test_valid_memory_is_retrievable_from_another_conversation(client, settings):
    source = create_conversation(client)
    other = create_conversation(client)
    message_id = add_message(settings.db_path, source["id"], "My sister Mara lives in Oslo.")
    memory = seed(
        settings.db_path,
        source["id"],
        "The user's sister Mara lives in Oslo.",
        keywords=("mara", "oslo"),
        message_ids=(message_id,),
    )
    assert other["id"] != source["id"]

    result = retrieve(settings, "How is my sister Mara?")

    assert [item.id for item in result.memories] == [memory.id]


def test_history_snapshots_do_not_re_expose_raw_source_references(client, settings):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "I live in Oslo.")
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user lives in Oslo.",
        message_ids=(message_id,),
    )

    entries = history_of(client, memory.id)
    serialized = json.dumps(entries)

    assert entries[0]["value"]["memory"]["statement"] == "The user lives in Oslo."
    assert "source_message_ids" not in serialized
    assert "source_conversation_id" not in serialized


def test_memory_without_surviving_source_is_marked_invalid(client, settings):
    conversation = create_conversation(client)
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user mentioned a now-deleted chat.",
    )
    with closing(sqlite3.connect(settings.db_path)) as raw:
        raw.execute("PRAGMA foreign_keys=ON")
        raw.execute("DELETE FROM conversations WHERE id = ?", (conversation["id"],))
        raw.commit()

    provenance = client.get(f"/api/memories/{memory.id}").json()["provenance"]

    assert provenance == {"conversation_id": None, "message_ids": [], "valid": False}


# ----------------------------------------------------------- confirm / reject ---

def test_confirm_review_memory(client, settings):
    conversation = create_conversation(client)
    memory = review_memory(settings.db_path, conversation["id"], "The user might like Oslo.")

    response = client.post(f"/api/memories/{memory.id}/confirm")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "confirmed"
    assert body["origin"] == "inferred"  # confirmation never upgrades origin
    assert body["last_confirmed_at"] is not None
    assert body["provenance"]["conversation_id"] == conversation["id"]

    entries = history_of(client, memory.id)
    assert [e["event"] for e in entries] == ["created", "corrected"]
    assert entries[-1]["value"]["status_from"] == "review"
    assert entries[-1]["value"]["status_to"] == "confirmed"


def test_confirm_accepted_memory_sets_last_confirmed(client, settings):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")

    response = client.post(f"/api/memories/{memory.id}/confirm")

    assert response.status_code == 200
    assert response.json()["status"] == "confirmed"
    assert response.json()["last_confirmed_at"] is not None


def test_confirm_records_reason(client, settings):
    conversation = create_conversation(client)
    memory = review_memory(settings.db_path, conversation["id"], "The user might like Oslo.")

    response = client.post(
        f"/api/memories/{memory.id}/confirm", json={"reason": "Confirmed after asking."}
    )

    assert response.status_code == 200
    assert history_of(client, memory.id)[-1]["value"]["reason"] == "Confirmed after asking."


def test_repeated_confirm_is_rejected_without_history(client, settings):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")
    assert client.post(f"/api/memories/{memory.id}/confirm").status_code == 200
    history_before = len(history_of(client, memory.id))

    response = client.post(f"/api/memories/{memory.id}/confirm")

    assert response.status_code == 409
    assert len(history_of(client, memory.id)) == history_before


def test_confirm_deleted_memory_is_rejected(client, settings):
    conversation = create_conversation(client)
    memory = review_memory(settings.db_path, conversation["id"], "The user might like Oslo.")
    assert client.delete(f"/api/memories/{memory.id}").status_code == 204

    response = client.post(f"/api/memories/{memory.id}/confirm")

    assert response.status_code == 409
    assert client.get(f"/api/memories/{memory.id}").json()["status"] == "deleted"


def test_reject_review_memory_soft_deletes_with_reason(client, settings):
    conversation = create_conversation(client)
    memory = review_memory(settings.db_path, conversation["id"], "The user might like Oslo.")

    response = client.post(f"/api/memories/{memory.id}/reject")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "deleted"
    assert body["origin"] == "inferred"
    entries = history_of(client, memory.id)
    assert entries[-1]["value"]["status_from"] == "review"
    assert entries[-1]["value"]["status_to"] == "deleted"
    assert entries[-1]["value"]["reason"] == "Rejected during review"


def test_reject_records_custom_reason(client, settings):
    conversation = create_conversation(client)
    memory = review_memory(settings.db_path, conversation["id"], "The user might like Oslo.")

    assert client.post(
        f"/api/memories/{memory.id}/reject", json={"reason": "Not true."}
    ).status_code == 200
    assert history_of(client, memory.id)[-1]["value"]["reason"] == "Not true."


def test_reject_repeated_or_accepted_memory_is_rejected(client, settings):
    conversation = create_conversation(client)
    memory = review_memory(settings.db_path, conversation["id"], "The user might like Oslo.")
    accepted = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")

    assert client.post(f"/api/memories/{memory.id}/reject").status_code == 200
    assert client.post(f"/api/memories/{memory.id}/reject").status_code == 409
    assert client.post(f"/api/memories/{accepted.id}/reject").status_code == 409


def test_review_and_rejected_memories_are_never_retrieved(client, settings):
    conversation = create_conversation(client)
    memory = review_memory(settings.db_path, conversation["id"], "The user might visit Oslo.")

    assert retrieve(settings, "oslo").memories == []  # review is excluded

    assert client.post(f"/api/memories/{memory.id}/reject").status_code == 200
    assert retrieve(settings, "oslo").memories == []

    drop_fts_index(settings.db_path)
    assert retrieve(settings, "oslo").memories == []  # fallback path too


def test_review_endpoints_cannot_be_told_what_status_to_set(client, settings):
    conversation = create_conversation(client)
    memory = review_memory(settings.db_path, conversation["id"], "The user might like Oslo.")

    for endpoint in ("confirm", "reject"):
        response = client.post(
            f"/api/memories/{memory.id}/{endpoint}",
            json={"status": "accepted", "origin": "user_stated", "confidence": 1.0},
        )
        assert response.status_code == 422, response.text

    stored = client.get(f"/api/memories/{memory.id}").json()
    assert stored["status"] == "review"
    assert stored["origin"] == "inferred"
    assert stored["confidence"] == 0.5


# --------------------------------------------------------------- corrections ---

def test_correction_updates_content_and_preserves_history(client, settings):
    conversation = create_conversation(client)
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user lives in Oslo.",
        subject="home",
        keywords=("oslo",),
    )

    response = client.patch(
        f"/api/memories/{memory.id}",
        json={"statement": "The user lives in Bergen.", "keywords": ["bergen", "home"]},
    )
    body = response.json()

    assert response.status_code == 200
    assert body["statement"] == "The user lives in Bergen."
    assert body["keywords"] == ["bergen", "home"]
    assert body["origin"] == "user_stated"
    assert body["status"] == "accepted"
    assert body["provenance"]["conversation_id"] == conversation["id"]

    entries = history_of(client, memory.id)
    assert entries[-1]["event"] == "corrected"
    assert entries[-1]["value"]["before"]["statement"] == "The user lives in Oslo."
    assert entries[-1]["value"]["after"]["statement"] == "The user lives in Bergen."


def test_correction_cannot_change_origin_status_or_provenance(client, settings):
    conversation = create_conversation(client)
    memory = review_memory(settings.db_path, conversation["id"], "The user might like Oslo.")

    response = client.patch(
        f"/api/memories/{memory.id}",
        json={"statement": "The user likes Oslo.", "status": "confirmed"},
    )

    assert response.status_code == 422
    stored = client.get(f"/api/memories/{memory.id}").json()
    assert stored["statement"] == "The user might like Oslo."
    assert stored["status"] == "review"
    assert stored["origin"] == "inferred"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"statement": "   "},
        {"statement": None},
        {"keywords": None},
        {"statement": "x" * (MAX_MEMORY_STATEMENT_CHARS + 1)},
        {"keywords": ["k" * 51]},
    ],
)
def test_invalid_corrections_are_rejected(client, settings, payload):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")

    response = client.patch(f"/api/memories/{memory.id}", json=payload)

    assert response.status_code == 422, response.text
    stored = client.get(f"/api/memories/{memory.id}").json()
    assert stored["statement"] == "The user lives in Oslo."
    assert stored["status"] == "accepted"
    assert stored["origin"] == "user_stated"
    assert len(stored["history"]) == 1  # only the creation entry


def test_null_statement_correction_is_rejected_without_mutation(client, settings):
    conversation = create_conversation(client)
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user lives in Oslo.",
        subject="home",
        keywords=("oslo",),
    )
    before = client.get(f"/api/memories/{memory.id}").json()

    response = client.patch(
        f"/api/memories/{memory.id}", json={"statement": None}
    )

    assert response.status_code == 422, response.text
    after = client.get(f"/api/memories/{memory.id}").json()
    assert after["statement"] == before["statement"]
    assert after["subject"] == before["subject"]
    assert after["keywords"] == before["keywords"]
    assert after["status"] == before["status"]
    assert after["origin"] == before["origin"]
    assert after["updated_at"] == before["updated_at"]
    assert after["history"] == before["history"]


def test_partial_correction_preserves_omitted_fields(client, settings):
    conversation = create_conversation(client)
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user lives in Oslo.",
        subject="home",
        keywords=("oslo", "home"),
    )

    response = client.patch(
        f"/api/memories/{memory.id}", json={"statement": "The user lives in Bergen."}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["statement"] == "The user lives in Bergen."
    assert body["subject"] == "home"
    assert body["keywords"] == ["oslo", "home"]
    assert body["origin"] == "user_stated"
    assert body["status"] == "accepted"


def test_correction_updates_future_retrieval(client, settings):
    conversation = create_conversation(client)
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user lives in Oslo.",
        keywords=("oslo",),
    )
    assert [item.id for item in retrieve(settings, "oslo").memories] == [memory.id]

    response = client.patch(
        f"/api/memories/{memory.id}",
        json={
            "statement": "The user lives in Bergen.",
            "keywords": ["bergen"],
        },
    )
    assert response.status_code == 200, response.text

    assert retrieve(settings, "oslo").memories == []
    assert [item.id for item in retrieve(settings, "bergen").memories] == [memory.id]


def test_statement_only_correction_updates_future_retrieval(client, settings):
    conversation = create_conversation(client)
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user lives in Oslo.",
        keywords=("home",),
    )
    assert [item.id for item in retrieve(settings, "oslo").memories] == [memory.id]

    response = client.patch(
        f"/api/memories/{memory.id}", json={"statement": "The user lives in Bergen."}
    )
    assert response.status_code == 200, response.text

    # No keyword edit: the statement update alone changes lexical retrieval.
    assert retrieve(settings, "oslo").memories == []
    assert [item.id for item in retrieve(settings, "bergen").memories] == [memory.id]


def test_correction_of_deleted_memory_is_rejected(client, settings):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")
    assert client.delete(f"/api/memories/{memory.id}").status_code == 204

    response = client.patch(f"/api/memories/{memory.id}", json={"statement": "Something else."})

    assert response.status_code == 409
    assert client.get(f"/api/memories/{memory.id}").json()["statement"] == "The user lives in Oslo."


def test_correction_of_superseded_memory_is_rejected(client, settings):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")
    with db_connection(settings.db_path) as conn:
        update_memory_status(conn, memory.id, MemoryStatusIn(status="superseded"))

    response = client.patch(f"/api/memories/{memory.id}", json={"statement": "New text."})

    assert response.status_code == 409


def test_correction_does_not_touch_messages_or_other_memories(client, settings):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "I live in Oslo.")
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user lives in Oslo.",
        message_ids=(message_id,),
    )
    other = seed(settings.db_path, conversation["id"], "The user likes tea.")

    assert client.patch(
        f"/api/memories/{memory.id}", json={"statement": "The user lives in Bergen."}
    ).status_code == 200

    with closing(sqlite3.connect(settings.db_path)) as raw:
        raw.row_factory = sqlite3.Row
        message = raw.execute("SELECT content FROM messages WHERE id = ?", (message_id,)).fetchone()
    assert message["content"] == "I live in Oslo."
    assert client.get(f"/api/memories/{other.id}").json()["statement"] == "The user likes tea."


def test_correction_survives_application_restart(client, settings, tmp_path):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")
    assert client.patch(
        f"/api/memories/{memory.id}", json={"statement": "The user lives in Bergen."}
    ).status_code == 200

    restarted = create_app(
        settings=Settings(db_path=settings.db_path, model_tag=TEST_MODEL_TAG),
        ollama=FakeOllama(),
    )
    with TestClient(restarted) as restarted_client:
        body = restarted_client.get(f"/api/memories/{memory.id}").json()

    assert body["statement"] == "The user lives in Bergen."
    assert body["history"][-1]["value"]["before"]["statement"] == "The user lives in Oslo."


# ------------------------------------------------------------------ deletion ---

def test_delete_soft_deletes_and_retains_history(client, settings):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")

    response = client.delete(f"/api/memories/{memory.id}")

    assert response.status_code == 204
    assert response.content == b""
    body = client.get(f"/api/memories/{memory.id}").json()
    assert body["status"] == "deleted"
    entries = history_of(client, memory.id)
    assert entries[-1]["value"]["status_to"] == "deleted"

    listed = client.get("/api/memories?status=deleted").json()
    assert [item["id"] for item in listed["items"]] == [memory.id]


def test_repeated_delete_is_idempotent(client, settings):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")

    assert client.delete(f"/api/memories/{memory.id}").status_code == 204
    history_after_first = len(history_of(client, memory.id))

    assert client.delete(f"/api/memories/{memory.id}").status_code == 204
    assert len(history_of(client, memory.id)) == history_after_first


@pytest.mark.parametrize("drop_fts", [False, True])
def test_deleted_memory_is_excluded_from_all_retrieval(
    client, settings, drop_fts
):
    conversation = create_conversation(client)
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user lives in Oslo.",
        keywords=("oslo",),
    )
    assert len(retrieve(settings, "oslo").memories) == 1

    assert client.delete(f"/api/memories/{memory.id}").status_code == 204
    if drop_fts:
        drop_fts_index(settings.db_path)

    assert retrieve(settings, "oslo").memories == []


def test_delete_does_not_delete_conversation_or_messages(client, settings):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "I live in Oslo.")
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user lives in Oslo.",
        message_ids=(message_id,),
    )

    assert client.delete(f"/api/memories/{memory.id}").status_code == 204

    assert client.get(f"/api/conversations/{conversation['id']}").status_code == 200
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [m["id"] for m in messages] == [message_id]


# ---------------------------------------------------------------- supersede ---

def test_supersede_accepted_memory_retains_record_and_excludes_retrieval(client, settings):
    conversation = create_conversation(client)
    message_id = add_message(settings.db_path, conversation["id"], "I live in Oslo.")
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user lives in Oslo.",
        keywords=("oslo",),
        message_ids=(message_id,),
    )
    assert len(retrieve(settings, "oslo").memories) == 1

    response = client.post(
        f"/api/memories/{memory.id}/supersede", json={"reason": "The user moved to Bergen."}
    )
    body = response.json()

    assert response.status_code == 200, response.text
    assert body["status"] == "superseded"
    assert body["statement"] == "The user lives in Oslo."
    assert body["origin"] == "user_stated"
    assert body["provenance"]["valid"] is True
    entries = history_of(client, memory.id)
    assert entries[-1]["value"] == {
        "status_from": "accepted",
        "status_to": "superseded",
        "reason": "The user moved to Bergen.",
    }
    assert retrieve(settings, "oslo").memories == []
    assert memory.id in list_ids(client, "?status=superseded")


def test_supersede_confirmed_memory_records_transition(client, settings):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")
    assert client.post(f"/api/memories/{memory.id}/confirm").status_code == 200

    response = client.post(f"/api/memories/{memory.id}/supersede")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "superseded"
    assert history_of(client, memory.id)[-1]["value"]["status_from"] == "confirmed"
    assert history_of(client, memory.id)[-1]["value"]["reason"] == "Marked outdated by user"


@pytest.mark.parametrize("status", ["review", "deleted", "superseded", "conflicted", "orphaned"])
def test_supersede_ineligible_status_is_rejected_without_mutation(
    client, settings, status
):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")
    with db_connection(settings.db_path) as conn:
        update_memory_status(conn, memory.id, MemoryStatusIn(status=status))
    before = client.get(f"/api/memories/{memory.id}").json()

    response = client.post(f"/api/memories/{memory.id}/supersede")

    assert response.status_code == 409
    after = client.get(f"/api/memories/{memory.id}").json()
    assert after["status"] == status
    assert after["updated_at"] == before["updated_at"]
    assert after["history"] == before["history"]


def test_supersede_missing_memory_returns_404(client):
    assert client.post(f"/api/memories/{MISSING_ID}/supersede").status_code == 404


def test_superseded_memory_is_excluded_from_like_fallback(client, settings):
    conversation = create_conversation(client)
    memory = seed(
        settings.db_path,
        conversation["id"],
        "The user lives in Oslo.",
        keywords=("oslo",),
    )
    assert client.post(f"/api/memories/{memory.id}/supersede").status_code == 200
    drop_fts_index(settings.db_path)

    assert retrieve(settings, "oslo").memories == []


# --------------------------------------------------- failures and regression ---

def test_storage_failure_returns_safe_500_and_no_mutation(client, settings, monkeypatch):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")

    def fail(*args, **kwargs):
        raise MemoryPersistenceError("Could not update the memory status.")

    monkeypatch.setattr(memory_service, "update_memory_status", fail)

    response = client.post(f"/api/memories/{memory.id}/confirm")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert "Could not" in detail
    assert "sqlite" not in detail.lower()
    assert str(settings.db_path) not in detail


def test_failed_history_write_rolls_back_the_correction(client, settings, monkeypatch):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(memory_service, "_record_history", fail)

    response = client.patch(
        f"/api/memories/{memory.id}", json={"statement": "The user lives in Bergen."}
    )

    assert response.status_code == 500
    monkeypatch.undo()

    with closing(sqlite3.connect(settings.db_path)) as raw:
        raw.row_factory = sqlite3.Row
        row = raw.execute(
            "SELECT statement FROM memories WHERE id = ?", (memory.id,)
        ).fetchone()
        history_count = raw.execute(
            "SELECT COUNT(*) AS n FROM memory_history WHERE memory_id = ?", (memory.id,)
        ).fetchone()["n"]
    assert row["statement"] == "The user lives in Oslo."
    assert history_count == 1  # only the creation entry


def test_malformed_correction_body_is_rejected(client, settings):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")

    response = client.patch(f"/api/memories/{memory.id}", json={"keywords": "oslo"})

    assert response.status_code == 422


def test_chat_and_extraction_still_work_after_memory_api_use(
    client, settings, fake_ollama
):
    conversation = create_conversation(client)
    memory = seed(settings.db_path, conversation["id"], "The user lives in Oslo.")

    assert client.patch(
        f"/api/memories/{memory.id}", json={"statement": "The user lives in Bergen."}
    ).status_code == 200

    fake_ollama.extraction_json = {"candidates": []}
    events = post_message_stream(client, conversation["id"], "Hello again!")

    assert events[-1]["type"] == "done"
    assert len(fake_ollama.chat_json_calls) == 1
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
