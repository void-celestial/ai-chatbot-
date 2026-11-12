"""Tests for the metadata-only generation timing log.

The line exists so a slow reply can be attributed to a cold model load versus
prompt prefill versus slow decoding without instrumenting the model again. It
must log numbers only — never message or memory text.
"""

import logging

from .conftest import FakeOllama, create_conversation, read_sse_events

TIMING_FIELDS = {
    "load_duration": 9_400_000_000,      # 9.4s cold load
    "prompt_eval_count": 285,
    "prompt_eval_duration": 160_000_000,  # 0.16s prefill
    "eval_count": 18,
    "eval_duration": 1_380_000_000,       # 1.38s decode -> 13.0 tok/s
}


class TimingFakeOllama(FakeOllama):
    """Fake that reports Ollama's real final-chunk timing fields."""

    async def chat_stream(self, model: str, messages, num_ctx: int):
        self.last_payload = {"model": model, "messages": messages, "num_ctx": num_ctx}
        yield {"message": {"content": "Hello"}}
        yield {"message": {"content": ""}, "done": True, **TIMING_FIELDS}


def _timing_lines(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "Generation timing" in r.getMessage()]


def test_reply_logs_load_prefill_and_decode_without_content(client, caplog):
    client.app.state.ollama = TimingFakeOllama()
    conversation = create_conversation(client)

    with caplog.at_level(logging.INFO, logger="backend.app.services.chat_service"):
        with client.stream(
            "POST",
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "Private marker-QQQ about my life"},
        ) as response:
            assert response.status_code == 200
            read_sse_events(response)

    lines = _timing_lines(caplog)
    assert len(lines) == 1
    line = lines[0]
    assert "load_ms=9400" in line
    assert "prefill_ms=160" in line
    assert "decode_tok_per_s=13.0" in line
    assert "prompt_tokens=285" in line
    assert "output_tokens=18" in line
    # Numbers only: no message text, and no memory evidence may reach the log.
    assert "marker-QQQ" not in line


def test_reply_still_logs_when_ollama_reports_no_stats(client, caplog):
    """A stream without timing fields must not raise or log misleading zeros."""
    conversation = create_conversation(client)

    with caplog.at_level(logging.INFO, logger="backend.app.services.chat_service"):
        with client.stream(
            "POST",
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "Hi"},
        ) as response:
            assert response.status_code == 200
            read_sse_events(response)

    lines = _timing_lines(caplog)
    assert len(lines) == 1
    assert "decode_tok_per_s=n/a" in lines[0]
    assert "load_ms=0" in lines[0]
