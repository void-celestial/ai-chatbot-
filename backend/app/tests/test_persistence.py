from .conftest import TEST_MODEL_TAG, FakeOllama, create_conversation, post_message_stream

from backend.app.config import Settings
from backend.app.main import create_app
from fastapi.testclient import TestClient


def test_data_survives_backend_restart(tmp_path):
    # Session 1: fresh app + database
    settings = Settings(db_path=tmp_path / "companion.db", model_tag=TEST_MODEL_TAG)
    app_a = create_app(settings=settings, ollama=FakeOllama())
    client_a = TestClient(app_a)
    conversation = create_conversation(client_a, title="Long-term chat")
    post_message_stream(client_a, conversation["id"], "Persist me across restarts")

    # Session 2: brand-new app instance over the same database file
    settings_b = Settings(db_path=tmp_path / "companion.db", model_tag=TEST_MODEL_TAG)
    app_b = create_app(settings=settings_b, ollama=FakeOllama())
    client_b = TestClient(app_b)

    listed = client_b.get("/api/conversations").json()
    assert [c["id"] for c in listed] == [conversation["id"]]
    assert listed[0]["title"] == "Long-term chat"
    assert listed[0]["message_count"] == 2

    messages = client_b.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [(m["role"], m["generation_status"]) for m in messages] == [
        ("user", None),
        ("assistant", "complete"),
    ]
    assert messages[0]["content"] == "Persist me across restarts"
    assert messages[0]["id"] == messages[0]["id"]  # stable IDs


def test_two_conversations_remain_independent(tmp_path):
    settings = Settings(db_path=tmp_path / "companion.db", model_tag=TEST_MODEL_TAG)
    app = create_app(settings=settings, ollama=FakeOllama())
    client = TestClient(app)

    conv_a = create_conversation(client, title="A")
    conv_b = create_conversation(client, title="B")
    post_message_stream(client, conv_a["id"], "Only in conversation A")

    messages_b = client.get(f"/api/conversations/{conv_b['id']}/messages").json()
    assert messages_b == []
    assert client.get(f"/api/conversations/{conv_b['id']}").json()["message_count"] == 0

    messages_a = client.get(f"/api/conversations/{conv_a['id']}/messages").json()
    assert [m["role"] for m in messages_a] == ["user", "assistant"]
