from .conftest import create_conversation, post_message_stream


def test_create_conversation_returns_201_with_fields(client):
    conversation = create_conversation(client, title="Evening chat")
    assert conversation["title"] == "Evening chat"
    assert conversation["status"] == "active"
    assert conversation["message_count"] == 0
    assert conversation["id"]
    # UTC ISO timestamps with "Z" suffix
    assert conversation["created_at"].endswith("Z")
    assert conversation["created_at"] == conversation["updated_at"]


def test_create_conversation_default_title(client):
    conversation = create_conversation(client)
    assert conversation["title"] == "New conversation"


def test_create_conversation_blank_title_becomes_default(client):
    response = client.post("/api/conversations", json={"title": "   "})
    assert response.status_code == 201
    assert response.json()["title"] == "New conversation"


def test_list_conversations_sorted_by_recent_activity(client):
    first = create_conversation(client, title="First")
    second = create_conversation(client, title="Second")

    # Activity on the first conversation should raise it to the top.
    post_message_stream(client, first["id"], "Hello there")

    listed = client.get("/api/conversations").json()
    assert [c["id"] for c in listed] == [first["id"], second["id"]]
    assert listed[0]["message_count"] >= 1


def test_get_missing_conversation_returns_404(client):
    assert client.get("/api/conversations/nonexistent").status_code == 404
    assert client.get("/api/conversations/nonexistent/messages").status_code == 404


def test_messages_retrieved_in_correct_order(client):
    conversation = create_conversation(client)
    post_message_stream(client, conversation["id"], "First user message")

    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    roles = [m["role"] for m in messages]
    contents = [m["content"] for m in messages]
    assert roles == ["user", "assistant"]
    assert contents[0] == "First user message"
    assert contents[1].startswith("Hello")
    # seq strictly increasing in returned order
    seqs = [m["seq"] for m in messages]
    assert seqs == sorted(seqs)
