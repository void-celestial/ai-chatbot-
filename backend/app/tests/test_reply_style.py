"""Tests for the opt-in reply-length guidance (Phase 3).

The default style must leave the system prompt exactly as it was; the concise
style adds one guidance section between the core rules and the persona block,
and it is guidance only (no token cap, no truncation).
"""

import json
import logging

import pytest

from backend.app.config import Settings, load_settings
from backend.app.models.schemas import PersonaOut
from backend.app.services.ollama_client import OllamaClient
from backend.app.services.prompt_builder import (
    ADAPTIVE_REPLY_GUIDANCE,
    BASE_BEHAVIOR_PROMPT,
    CONCISE_REPLY_GUIDANCE,
    build_system_prompt,
)
from .conftest import create_conversation, read_sse_events

PERSONA = PersonaOut(
    name="Vex",
    tone="warm",
    formality="casual",
    custom_instructions="Prefers dry humour.",
    created_at="2026-01-01T00:00:00+00:00",
    updated_at="2026-01-01T00:00:00+00:00",
)


def test_default_style_emits_the_original_prompt_unchanged():
    prompt = build_system_prompt(PERSONA, [], memory_section=True)
    assert "## Reply length" not in prompt
    assert CONCISE_REPLY_GUIDANCE not in prompt
    # Same shape and order as before the feature existed.
    assert prompt.startswith("## Core behavior rules\n" + BASE_BEHAVIOR_PROMPT)
    assert prompt.index("## Persona settings") < prompt.index("## Memory evidence")


def test_concise_style_inserts_guidance_between_rules_and_persona():
    prompt = build_system_prompt(PERSONA, [], memory_section=True, reply_style="concise")
    assert CONCISE_REPLY_GUIDANCE in prompt
    rules = prompt.index("## Core behavior rules")
    length = prompt.index("## Reply length")
    persona = prompt.index("## Persona settings")
    memory = prompt.index("## Memory evidence")
    assert rules < length < persona < memory
    # Guidance, not a cap: it must ask for detail where the question needs it
    # and must not impose a fixed sentence or token budget on every reply.
    lowered = CONCISE_REPLY_GUIDANCE.lower()
    assert "never cut a thought short" in lowered
    assert "token" not in lowered
    assert "num_predict" not in lowered


def test_concise_style_keeps_persona_and_memory_layers():
    prompt = build_system_prompt(PERSONA, [], memory_section=True, reply_style="concise")
    assert "Your companion name is “Vex”" in prompt
    assert "Prefers dry humour." in prompt
    assert "No stored memories are included" in prompt


@pytest.mark.parametrize("raw,expected", [
    ("concise", "concise"),
    ("CONCISE", "concise"),
    ("default", "default"),
    ("adaptive", "adaptive"),
    (" Adaptive ", "adaptive"),
    ("ADAPTIVE", "adaptive"),
    ("shorter", "default"),
    ("verbose", "default"),
    ("", "default"),
])
def test_reply_style_env_is_validated(monkeypatch, raw, expected):
    monkeypatch.setenv("OLLAMA_REPLY_STYLE", raw)
    assert load_settings().reply_style == expected


def test_reply_style_defaults_to_previous_behavior():
    assert Settings().reply_style == "default"
    assert Settings().max_output_tokens == 0  # still no output cap


def test_concise_style_reaches_the_ollama_request(client, caplog):
    """Integration: the configured style must actually shape the system prompt."""
    client.app.state.settings = Settings(
        db_path=client.app.state.settings.db_path,
        model_tag=client.app.state.settings.model_tag,
        reply_style="concise",
    )
    conversation = create_conversation(client)
    with caplog.at_level(logging.INFO):
        with client.stream(
            "POST",
            f"/api/conversations/{conversation['id']}/messages",
            json={"content": "hey, how's it going?"},
        ) as response:
            assert response.status_code == 200
            read_sse_events(response)

    system = client.app.state.ollama.last_payload["messages"][0]
    assert system["role"] == "system"
    assert "## Reply length" in system["content"]
    # Style guidance must not become stored memory or a persisted message.
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert all("## Reply length" not in m["content"] for m in messages)


def test_default_style_keeps_system_prompt_free_of_guidance(client):
    conversation = create_conversation(client)
    with client.stream(
        "POST",
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "hey"},
    ) as response:
        read_sse_events(response)
    system = client.app.state.ollama.last_payload["messages"][0]["content"]
    assert "## Reply length" not in system


# ------------------------------------------------------ adaptive (Phase 4) ---

# Frozen copy of the Phase 3 concise guidance. `concise` was measured with
# exactly this text, so it must not drift; changing it on purpose means
# updating this snapshot deliberately.
PHASE3_CONCISE_GUIDANCE = (
    "## Reply length\n"
    "Match the length of a reply to what the message actually needs. For a greeting, "
    "small talk, or a simple factual question, answer in one to three sentences and "
    "stop there: no recap, no bullet lists, no offers to help further. For a technical "
    "or multi-part question, a decision someone is weighing, or emotional support, use "
    "the space the answer genuinely needs: give the explanation, the caveats that "
    "change the answer, and the next step, and never cut a thought short to save length. "
    "Do not pad any reply with restatements of the question or closing pleasantries."
)


def test_concise_guidance_and_prompt_are_unchanged_since_phase_3():
    assert CONCISE_REPLY_GUIDANCE == PHASE3_CONCISE_GUIDANCE
    concise = build_system_prompt(PERSONA, [], memory_section=True, reply_style="concise")
    adaptive = build_system_prompt(PERSONA, [], memory_section=True, reply_style="adaptive")
    # Each style emits exactly one guidance block, and never the other style's.
    assert concise.count("## Reply length") == 1
    assert ADAPTIVE_REPLY_GUIDANCE not in concise
    assert CONCISE_REPLY_GUIDANCE not in adaptive


def test_default_prompt_still_contains_no_length_guidance():
    prompt = build_system_prompt(PERSONA, [], memory_section=True)
    for guidance in (CONCISE_REPLY_GUIDANCE, ADAPTIVE_REPLY_GUIDANCE):
        assert guidance not in prompt
    assert "## Reply length" not in prompt


def test_adaptive_guidance_sits_between_rules_and_persona():
    prompt = build_system_prompt(PERSONA, [], memory_section=True, reply_style="adaptive")
    rules = prompt.index("## Core behavior rules")
    length = prompt.index("## Reply length")
    persona = prompt.index("## Persona settings")
    memory = prompt.index("## Memory evidence")
    assert rules < length < persona < memory
    assert prompt.count("## Reply length") == 1
    # Persona and memory layers are untouched by the style.
    assert "Your companion name is “Vex”" in prompt
    assert "Prefers dry humour." in prompt
    assert "No stored memories are included" in prompt


def test_adaptive_prefers_brevity_for_casual_messages():
    text = ADAPTIVE_REPLY_GUIDANCE.lower()
    for marker in ("greetings", "banter", "simple questions", "everyday exchanges"):
        assert marker in text
    assert "one to three sentences" in text
    assert "no lists" in text and "no preamble" in text


def test_adaptive_keeps_detail_for_technical_and_complex_questions():
    text = ADAPTIVE_REPLY_GUIDANCE.lower()
    assert "technical questions" in text
    assert "example" in text and "caveats" in text and "steps" in text
    assert "do not leave out detail" in text
    assert "trade-offs" in text and "uncertainty" in text and "next step" in text
    assert "shallow answer is worse" in text


def test_adaptive_prioritises_empathy_over_arbitrary_brevity():
    text = ADAPTIVE_REPLY_GUIDANCE.lower()
    assert "emotional messages" in text and "venting" in text
    assert "stay with the feeling" in text
    assert "never become cold" in text
    assert "do not rush to fix the problem" in text
    assert "profanity" in text  # mirroring the user's register, not policing it


def test_adaptive_defers_to_explicit_user_length_requests():
    text = ADAPTIVE_REPLY_GUIDANCE.lower()
    assert "explicit length or depth request outranks" in text
    for phrase in (
        '"keep it short"',
        '"explain in detail"',
        '"give me just the answer"',
        '"walk me through it"',
    ):
        assert phrase in text


def test_adaptive_uses_conversation_context_without_false_memory_claims():
    text = ADAPTIVE_REPLY_GUIDANCE.lower()
    assert "follow-up" in text
    assert "skip background the user already knows" in text
    assert "never claim to remember or forget something without evidence" in text


def test_adaptive_introduces_no_token_cap():
    settings = Settings(reply_style="adaptive")
    assert settings.max_output_tokens == 0
    payload = OllamaClient(settings).build_payload(
        "qwen3.5:9b", [{"role": "user", "content": "hi"}], 8192, stream=True
    )
    assert payload["options"] == {"num_ctx": 8192}
    assert "num_predict" not in payload["options"]
    text = ADAPTIVE_REPLY_GUIDANCE.lower()
    assert "num_predict" not in text and "token" not in text


def test_adaptive_style_reaches_request_and_is_not_persisted(client):
    client.app.state.settings = Settings(
        db_path=client.app.state.settings.db_path,
        model_tag=client.app.state.settings.model_tag,
        reply_style="adaptive",
    )
    conversation = create_conversation(client)
    with client.stream(
        "POST",
        f"/api/conversations/{conversation['id']}/messages",
        json={"content": "hey, how's it going?"},
    ) as response:
        assert response.status_code == 200
        events = read_sse_events(response)

    system = client.app.state.ollama.last_payload["messages"][0]
    assert system["role"] == "system"
    assert ADAPTIVE_REPLY_GUIDANCE in system["content"]
    # Streaming still works end to end under the adaptive style.
    assert sum(1 for e in events if e["type"] == "delta") >= 2
    assert any(e["type"] == "done" for e in events)

    # Guidance lives only in the system prompt: never a message or memory.
    messages = client.get(f"/api/conversations/{conversation['id']}/messages").json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert all("## Reply length" not in m["content"] for m in messages)
    assert all("Reply length" not in json.dumps(m.get("metadata") or {}) for m in messages)
    memories = client.get("/api/memories").json()
    assert all("## Reply length" not in item.get("statement", "") for item in memories.get("items", []))
