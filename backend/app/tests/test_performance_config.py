"""Regression tests for the Ollama lifecycle options added for responsiveness.

These pin the request shape the app sends to Ollama: `keep_alive` is always
present, `think` is only sent when explicitly configured, and `num_predict` is
only sent when a positive cap is configured. Defaults must keep the previous
behavior (no output cap, model's own thinking default).
"""

from backend.app.config import Settings
from backend.app.services.ollama_client import OllamaClient

MESSAGES = [{"role": "user", "content": "hello"}]


def test_defaults_preserve_previous_generation_behavior():
    settings = Settings()
    assert settings.ollama_keep_alive == "30m"
    assert settings.ollama_think is None
    assert settings.max_output_tokens == 0
    assert settings.num_ctx == 8192
    assert settings.model_tag == "qwen3.5:9b"


def test_stream_payload_carries_keep_alive_and_omits_unset_flags():
    payload = OllamaClient(Settings()).build_payload(
        "qwen3.5:9b", MESSAGES, 8192, stream=True
    )
    assert payload["model"] == "qwen3.5:9b"
    assert payload["messages"] == MESSAGES
    assert payload["stream"] is True
    assert payload["keep_alive"] == "30m"
    assert payload["options"] == {"num_ctx": 8192}
    assert "think" not in payload
    assert "format" not in payload


def test_thinking_flag_is_sent_only_when_configured():
    off = OllamaClient(Settings(ollama_think=False)).build_payload(
        "qwen3.5:9b", MESSAGES, 8192, stream=True
    )
    assert off["think"] is False

    on = OllamaClient(Settings(ollama_think=True)).build_payload(
        "qwen3.5:9b", MESSAGES, 8192, stream=True
    )
    assert on["think"] is True


def test_output_cap_is_only_sent_when_positive():
    capped = OllamaClient(Settings(max_output_tokens=512)).build_payload(
        "qwen3.5:9b", MESSAGES, 8192, stream=True
    )
    assert capped["options"]["num_predict"] == 512

    unlimited = OllamaClient(Settings(max_output_tokens=0)).build_payload(
        "qwen3.5:9b", MESSAGES, 8192, stream=True
    )
    assert "num_predict" not in unlimited["options"]


def test_json_payload_used_by_memory_extraction_matches_stream_policy():
    client = OllamaClient(
        Settings(ollama_keep_alive="15m", ollama_think=False, max_output_tokens=256)
    )
    payload = client.build_payload(
        "qwen3.5:9b", MESSAGES, 4096, stream=False, json_format=True
    )
    assert payload["stream"] is False
    assert payload["format"] == "json"
    assert payload["keep_alive"] == "15m"
    assert payload["think"] is False
    assert payload["options"] == {"num_ctx": 4096, "num_predict": 256}


def test_custom_keep_alive_is_forwarded_verbatim():
    payload = OllamaClient(Settings(ollama_keep_alive="0")).build_payload(
        "qwen3.5:9b", MESSAGES, 8192, stream=True
    )
    # Ollama accepts per-request durations; "0" unloads immediately.
    assert payload["keep_alive"] == "0"


# ------------------------------------------------- connection/preflight cost ---

def test_default_base_url_targets_ipv4_loopback():
    # Ollama binds 127.0.0.1 only; "localhost" first tries the unreachable
    # ::1 and pays a ~0.25s fallback delay on every fresh connection.
    assert Settings().ollama_base_url == "http://127.0.0.1:11434"
    from backend.app.config import load_settings

    assert load_settings().ollama_base_url == "http://127.0.0.1:11434"


def test_base_url_env_override_still_wins(monkeypatch):
    from backend.app.config import load_settings

    monkeypatch.setenv("OLLAMA_BASE_URL", "http://192.168.1.10:11434")
    assert load_settings().ollama_base_url == "http://192.168.1.10:11434"


def test_healthy_model_preflight_is_cached():
    import asyncio

    client = OllamaClient(Settings(model_tag="qwen3.5:9b"))
    calls: list[int] = []

    async def fake_fetch():
        calls.append(1)
        return ["qwen3.5:9b", "other:latest"]

    client._fetch_models = fake_fetch  # type: ignore[method-assign]

    first = asyncio.run(client.list_models())
    second = asyncio.run(client.list_models())

    assert first == second == ["qwen3.5:9b", "other:latest"]
    assert len(calls) == 1  # second call answered from cache


def test_missing_model_answer_is_never_cached():
    import asyncio

    client = OllamaClient(Settings(model_tag="not-installed:latest"))
    calls: list[int] = []

    async def fake_fetch():
        calls.append(1)
        return ["other:latest"]

    client._fetch_models = fake_fetch  # type: ignore[method-assign]

    assert asyncio.run(client.list_models()) == ["other:latest"]
    assert asyncio.run(client.list_models()) == ["other:latest"]
    assert len(calls) == 2  # negative answers are re-checked every time


def test_preflight_cache_expires(monkeypatch):
    import asyncio
    import time

    from backend.app.services.ollama_client import MODELS_CACHE_SECONDS

    assert MODELS_CACHE_SECONDS > 0
    client = OllamaClient(Settings(model_tag="qwen3.5:9b"))
    calls: list[int] = []

    async def fake_fetch():
        calls.append(1)
        return ["qwen3.5:9b"]

    client._fetch_models = fake_fetch  # type: ignore[method-assign]
    asyncio.run(client.list_models())
    # Backdate the cached entry beyond the TTL: the next call refetches.
    client._models_cache = (time.monotonic() - MODELS_CACHE_SECONDS - 1, ["qwen3.5:9b"])
    asyncio.run(client.list_models())
    assert len(calls) == 2


def test_preflight_failures_are_never_cached():
    import asyncio

    from backend.app.services.ollama_client import OllamaUnavailable

    client = OllamaClient(Settings(model_tag="qwen3.5:9b"))
    calls: list[int] = []

    async def failing_fetch():
        calls.append(1)
        raise OllamaUnavailable("Ollama is down")

    client._fetch_models = failing_fetch  # type: ignore[method-assign]

    for _ in range(2):
        try:
            asyncio.run(client.list_models())
        except OllamaUnavailable:
            pass
        else:  # pragma: no cover - failure must propagate
            raise AssertionError("expected OllamaUnavailable")
    assert len(calls) == 2  # failures propagate and are not cached
