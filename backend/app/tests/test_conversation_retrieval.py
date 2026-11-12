"""Regression tests for reading conversations back out of the local database.

The UI symptom was "history cannot be retrieved"; these tests pin the backend
half of that flow: a healthy database returns the stored list, a genuinely
empty database returns an empty list (not an error), malformed or injection
style ids cannot change a query, and a database fault becomes a sanitized 500
rather than a crash or an empty list.
"""

import sqlite3

import pytest

from .conftest import create_conversation, post_message_stream


def test_list_returns_stored_conversations(client):
    first = create_conversation(client, title="Stored locally")
    post_message_stream(client, first["id"], "Remember this exchange")

    response = client.get("/api/conversations")
    assert response.status_code == 200
    body = response.json()
    assert [c["id"] for c in body] == [first["id"]]
    assert body[0]["title"] == "Stored locally"
    assert body[0]["message_count"] == 2
    assert set(body[0]) == {
        "id",
        "title",
        "status",
        "created_at",
        "updated_at",
        "message_count",
    }


def test_empty_database_returns_an_empty_list(client):
    response = client.get("/api/conversations")
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.parametrize(
    "conversation_id",
    [
        "1' OR '1'='1",
        "1 OR 1=1",
        "'; DROP TABLE conversations; --",
        "%27%20OR%201%3D1",
        "../../data/companion.db",
        "nonexistent",
    ],
)
def test_unknown_or_injection_style_ids_return_404_without_changing_data(client, conversation_id):
    conversation = create_conversation(client, title="Keep me")
    post_message_stream(client, conversation["id"], "Only message")

    assert client.get(f"/api/conversations/{conversation_id}").status_code == 404
    assert client.get(f"/api/conversations/{conversation_id}/messages").status_code == 404

    # The store is unchanged: one active conversation with its two messages.
    listed = client.get("/api/conversations").json()
    assert [c["id"] for c in listed] == [conversation["id"]]
    assert listed[0]["message_count"] == 2
    assert client.get(f"/api/conversations/{conversation['id']}/messages").status_code == 200


@pytest.mark.parametrize("query", ["?limit=0", "?limit=501", "?limit=abc", "?limit=-3"])
def test_list_rejects_out_of_range_or_malformed_limits(client, query):
    response = client.get(f"/api/conversations{query}")
    assert response.status_code == 422
    body = response.json()
    # Validation errors never echo the database path or a stack trace.
    assert "Traceback" not in str(body)
    assert "sqlite" not in str(body).lower()


def test_database_failure_is_reported_as_a_sanitized_500(client, monkeypatch):
    import backend.app.api.conversations as conversations_module

    def broken_connection(_db_path):
        raise sqlite3.OperationalError("unable to open database file: C:/secret/path.db")

    monkeypatch.setattr(conversations_module, "db_connection", broken_connection)

    response = client.get("/api/conversations")
    assert response.status_code == 500
    assert response.json() == {"detail": "Could not read stored conversations."}
    assert "secret" not in response.text
    assert "OperationalError" not in response.text


def test_conversations_are_scoped_to_their_own_messages(client):
    conversation_a = create_conversation(client, title="A")
    conversation_b = create_conversation(client, title="B")
    post_message_stream(client, conversation_a["id"], "Belongs to A only")

    messages_b = client.get(f"/api/conversations/{conversation_b['id']}/messages")
    assert messages_b.status_code == 200
    assert messages_b.json() == []

    messages_a = client.get(f"/api/conversations/{conversation_a['id']}/messages").json()
    assert {m["conversation_id"] for m in messages_a} == {conversation_a["id"]}


def test_default_settings_stay_local_only():
    from backend.app.config import Settings

    defaults = Settings()
    assert defaults.host == "127.0.0.1"
    assert defaults.port == 8000
    assert defaults.model_tag == "qwen3.5:9b"
    assert defaults.cors_origins == ("http://localhost:5173", "http://127.0.0.1:5173")


def test_cors_allows_the_local_dev_origin_only(client):
    preflight = {
        "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "content-type",
        "Origin": "http://localhost:5173",
    }
    allowed = client.options("/api/conversations", headers=preflight)
    assert allowed.status_code in (200, 204)
    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:5173"
    assert allowed.headers.get("access-control-allow-credentials") in (None, "false")

    for blocked_origin in ("http://evil.example", "http://192.168.1.20:5173"):
        blocked = client.options(
            "/api/conversations",
            headers={**preflight, "Origin": blocked_origin},
        )
        assert blocked.headers.get("access-control-allow-origin") is None
