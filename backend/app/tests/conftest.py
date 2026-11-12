import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.config import Settings  # noqa: E402
from backend.app.main import create_app  # noqa: E402
from backend.app.services.ollama_client import (  # noqa: E402
    GenerationFailed,
    ModelNotFound,
    OllamaError,
    OllamaUnavailable,
)
from backend.app.services.transcription import (  # noqa: E402
    TranscriptionFailure,
    TranscriptionOutcome,
)

TEST_MODEL_TAG = "fake-model:latest"


class FakeOllama:
    """Deterministic stand-in matching the OllamaClient interface."""

    def __init__(self) -> None:
        self.models: list[str] = [TEST_MODEL_TAG]
        self.behavior: str = "ok"  # ok | unavailable | model_missing | fail_after_chunks | empty
        self.chunks: list[str] = ["Hello", "!", " It is good to see you again."]
        self.last_payload: dict[str, Any] | None = None
        # Memory extraction (Phase 2, Step 4): JSON payload string/dict returned
        # by chat_json, plus an independent failure knob so extraction faults
        # can be tested without changing chat behavior.
        self.extraction_json: Any = {"candidates": []}
        self.extract_behavior: str = "ok"  # ok | unavailable | fail
        self.last_extract_request: dict[str, Any] | None = None
        self.chat_json_calls: list[dict[str, Any]] = []

    async def list_models(self) -> list[str]:
        if self.behavior == "unavailable":
            raise OllamaUnavailable("Cannot reach Ollama at http://localhost:11434.")
        if self.behavior == "model_missing":
            return ["some-other-model:latest"]
        return list(self.models)

    async def chat_stream(self, model: str, messages: list[dict[str, str]], num_ctx: int):
        self.last_payload = {"model": model, "messages": messages, "num_ctx": num_ctx}
        if self.behavior in ("unavailable", "model_missing"):
            raise OllamaUnavailable("Cannot reach Ollama at http://localhost:11434.")
        for message in self.chunks:
            yield {"message": {"content": message}}
            if self.behavior == "fail_after_chunks" and message == self.chunks[0]:
                raise GenerationFailed("Boom during generation.")
        if self.behavior == "empty":
            return
        yield {"message": {"content": ""}, "done": True}

    async def chat_json(
        self,
        model: str,
        messages: list[dict[str, str]],
        num_ctx: int,
        timeout_seconds: float | None = None,
    ) -> str:
        """Single-shot JSON call used by memory extraction; never real network."""
        self.last_extract_request = {
            "model": model,
            "messages": messages,
            "num_ctx": num_ctx,
            "timeout_seconds": timeout_seconds,
        }
        self.chat_json_calls.append(self.last_extract_request)
        if self.extract_behavior == "unavailable":
            raise OllamaUnavailable("Cannot reach Ollama at http://localhost:11434.")
        if self.extract_behavior == "fail":
            raise GenerationFailed("Extraction call failed.")
        payload = self.extraction_json
        if isinstance(payload, str):
            return payload  # caller treats the string as raw, untrusted model output
        return json.dumps(payload)


class FakeTranscriptionEngine:
    """Deterministic stand-in for the local ffmpeg/whisper engine."""

    def __init__(self) -> None:
        self.availability_reason: str | None = None
        self.behavior: str = "ok"  # ok | fail | timeout
        self.text: str = "This is a local test transcript."
        self.language: str | None = "en"
        self.calls: list[Path] = []

    def availability(self, settings: Settings) -> str | None:
        return self.availability_reason

    async def transcribe(self, settings: Settings, audio_path: Path) -> TranscriptionOutcome:
        self.calls.append(audio_path)
        if self.behavior == "fail":
            raise TranscriptionFailure("engine_failed")
        if self.behavior == "timeout":
            raise TranscriptionFailure("timeout")
        return TranscriptionOutcome(text=self.text, language=self.language)


class ModelMissingChatStub(FakeOllama):
    """Tags mismatch: list_models excludes the configured tag, so the reply
    path is never reached; chat_stream raises ModelNotFound if it were called."""

    async def list_models(self) -> list[str]:
        return ["some-other-model:latest"]

    async def chat_stream(self, model: str, messages: list[dict[str, str]], num_ctx: int):
        raise ModelNotFound(f"Model '{model}' was not found in Ollama")


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "companion-test.db",
        model_tag=TEST_MODEL_TAG,
        voice_notes_dir=tmp_path / "voice_notes",
        whisper_model_path=tmp_path / "models" / "ggml-base.en.bin",
    )


@pytest.fixture
def fake_ollama() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def fake_transcription() -> FakeTranscriptionEngine:
    return FakeTranscriptionEngine()


@pytest.fixture
def app(settings: Settings, fake_ollama: FakeOllama, fake_transcription: FakeTranscriptionEngine):
    return create_app(
        settings=settings, ollama=fake_ollama, transcription=fake_transcription
    )


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app, follow_redirects=False)


def create_conversation(client: TestClient, title: str | None = None) -> dict:
    body = {"title": title} if title is not None else {}
    response = client.post("/api/conversations", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def read_sse_events(response) -> list[dict]:
    events = []
    for line in response.iter_lines():
        line = line.strip()
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def post_message_stream(client: TestClient, conversation_id: str, content: str):
    with client.stream(
        "POST",
        f"/api/conversations/{conversation_id}/messages",
        json={"content": content},
    ) as response:
        if response.status_code != 200:
            response.read()
            pytest.fail(f"Expected 200 SSE, got {response.status_code}: {response.text}")
        assert response.headers["content-type"].startswith("text/event-stream")
        return read_sse_events(response)
