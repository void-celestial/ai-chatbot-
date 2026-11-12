import pytest

from .conftest import create_conversation, read_sse_events


def test_send_message_persists_user_and_valid_assistant_reply(client, fake_ollama):
    conversation = create_conversation(client)
    events = []
    with client.stream(
        "POST",
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Hi! Quick reminder test."},
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = read_sse_events(response)

    types = [e["type"] for e in events]
    assert "user_message" in types
    assert "assistant_started" in types
    assert "done" in types
    deltas = "".join(e["content"] for e in events if e["type"] == "delta")
    assert deltas.startswith("Hello!")

    done_event = next(e for e in events if e["type"] == "done")
    assert done_event["message"]["generation_status"] == "complete"
    assert done_event["message"]["model_tag"] == "fake-model:latest"

    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [(m["role"], m["generation_status"]) for m in messages] == [
        ("user", None),
        ("assistant", "complete"),
    ]


def test_prompt_includes_system_and_history_in_order(client, fake_ollama):
    conversation = create_conversation(client)
    with client.stream(
        "POST",
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Remember this marker-XYZ"},
    ) as response:
        assert response.status_code == 200
        read_sse_events(response)

    payload = fake_ollama.last_payload
    roles = [m["role"] for m in payload["messages"]]
    # The assistant reply is persisted only after generation, so the prompt
    # contains the system prompt plus the conversation history up to and
    # including the new user message.
    assert roles == ["system", "user"]
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][-1]["content"] == "Remember this marker-XYZ"
    assert "never invent memories" in payload["messages"][0]["content"]


@pytest.mark.parametrize("bad_payload", [{"content": ""}, {"content": "   "}])
def test_empty_or_blank_message_rejected(client, bad_payload):
    conversation = create_conversation(client)
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages", json=bad_payload
    )
    assert response.status_code == 422


def test_message_to_missing_conversation_returns_404(client):
    response = client.post(
        "/api/conversations/nonexistent/messages", json={"content": "hello"}
    )
    assert response.status_code == 404


def test_oversized_message_rejected(client):
    conversation = create_conversation(client)
    response = client.post(
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "x" * 20_001},
    )
    assert response.status_code == 422


def test_ollama_unavailable_yields_error_and_keeps_user_message(client, fake_ollama):
    fake_ollama.behavior = "unavailable"
    conversation = create_conversation(client)
    with client.stream(
        "POST",
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Are you there?"},
    ) as response:
        assert response.status_code == 200
        events = read_sse_events(response)

    user_event = next(e for e in events if e["type"] == "user_message")
    error_event = next(e for e in events if e["type"] == "error")
    assert error_event["code"] == "ollama_unavailable"
    assert "ollama" in error_event["message"].lower()
    assert user_event["message"]["content"] == "Are you there?"

    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user"]  # user kept, no fake assistant reply


def test_model_missing_yields_clear_error_and_keeps_user_message(client):
    from .conftest import ModelMissingChatStub

    conversation = create_conversation(client)
    # Swap the fake for the model-missing variant
    model_missing = ModelMissingChatStub()
    client.app.state.ollama = model_missing

    with client.stream(
        "POST",
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Who am I to you?"},
    ) as response:
        assert response.status_code == 200
        events = read_sse_events(response)

    error_event = next(e for e in events if e["type"] == "error")
    assert error_event["code"] == "model_not_found"
    assert "is not available in ollama" in error_event["message"].lower()

    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user"]


def test_failed_generation_keeps_user_message_without_assistant_record(client, fake_ollama):
    fake_ollama.behavior = "fail_after_chunks"
    conversation = create_conversation(client)
    with client.stream(
        "POST",
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Tell me something"},
    ) as response:
        assert response.status_code == 200
        events = read_sse_events(response)

    error_event = next(e for e in events if e["type"] == "error")
    assert error_event["code"] == "generation_failed"
    assert not any(e["type"] == "done" for e in events)

    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user"]


def test_retry_generates_reply_after_failure(client, fake_ollama):
    fake_ollama.behavior = "fail_after_chunks"
    conversation = create_conversation(client)
    with client.stream(
        "POST",
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "Tell me something"},
    ) as response:
        read_sse_events(response)  # fails

    fake_ollama.behavior = "ok"  # model recovers
    with client.stream(
        "POST", f"/api/conversations/{conversation['id']}/retry"
    ) as response:
        assert response.status_code == 200
        events = read_sse_events(response)

    assert any(e["type"] == "done" for e in events)
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]


def test_retry_without_user_message_returns_400(client):
    conversation = create_conversation(client)
    response = client.post(f"/api/conversations/{conversation['id']}/retry")
    assert response.status_code == 400
