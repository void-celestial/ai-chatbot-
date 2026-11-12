from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..config import (
    MAX_MEMORY_KEYWORDS,
    MAX_TRANSCRIPT_CHARS,
    MAX_VOICE_NOTE_TITLE_CHARS,
    MAX_MEMORY_KEYWORD_CHARS,
    MAX_MEMORY_SOURCE_MESSAGE_REFS,
    MAX_MEMORY_STATEMENT_CHARS,
    MAX_MEMORY_STATUS_REASON_CHARS,
    MAX_MEMORY_SUBJECT_CHARS,
    MAX_MESSAGE_CHARS,
    MAX_PERSONA_INSTRUCTIONS_CHARS,
    MAX_PERSONA_NAME_CHARS,
)


class ConversationOut(BaseModel):
    id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    message_count: int = 0


class MessageOut(BaseModel):
    """One stored message.

    `metadata` is decoded from `messages.metadata_json`. Phase 3 stores a
    bounded `memory_evidence` trace there for assistant replies: the memory
    records that were actually supplied as prompt evidence, including their
    validated source ids. It is response metadata, never prompt instructions.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    seq: int
    role: str
    content: str
    created_at: str
    model_tag: str | None = None
    generation_status: str | None = None
    metadata: dict | None = None


class CreateConversationIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class SendMessageIn(BaseModel):
    content: str = Field(max_length=MAX_MESSAGE_CHARS)

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message content must not be empty")
        return value.strip()


class PersonaOut(BaseModel):
    """Current companion persona settings, as stored in the database."""

    name: str
    tone: str
    formality: str
    custom_instructions: str | None
    created_at: str
    updated_at: str


class PersonaUpdateIn(BaseModel):
    """Partial persona update (PATCH semantics).

    Omitted fields keep their stored values. Explicit null semantics:
    - `custom_instructions: null` (or an empty/whitespace string) clears the
      instructions explicitly.
    - Null for name/tone/formality is rejected with 422: those persona columns
      are NOT NULL and there is no meaningful "unset" name or style, so the
      client must omit the field to keep the current value instead.
    """

    name: str | None = Field(default=None, max_length=MAX_PERSONA_NAME_CHARS)
    tone: Literal["warm", "calm", "playful", "direct"] | None = None
    formality: Literal["casual", "neutral", "formal"] | None = None
    custom_instructions: str | None = None

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError(
                "Companion name must not be null; omit the field to keep the current name"
            )
        stripped = value.strip()
        if not stripped:
            raise ValueError("Companion name must not be empty")
        return stripped

    @field_validator("tone")
    @classmethod
    def not_null_tone(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError(
                "Tone must not be null; omit the field to keep the current tone"
            )
        return value

    @field_validator("formality")
    @classmethod
    def not_null_formality(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError(
                "Formality must not be null; omit the field to keep the current formality"
            )
        return value

    @field_validator("custom_instructions")
    @classmethod
    def clean_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) > MAX_PERSONA_INSTRUCTIONS_CHARS:
            raise ValueError(
                f"Custom instructions must be at most {MAX_PERSONA_INSTRUCTIONS_CHARS} characters"
            )
        return stripped


# ---------------------------------------------------------------------------
# Structured memories (Phase 2 storage foundation).
#
# These models mirror migration 0002 exactly: kind ∈ (fact, preference, goal,
# event, person, other); origin ∈ (user_stated, inferred); status lifecycle
# (accepted, review, confirmed, conflicted, superseded, orphaned, deleted).
# Provenance (source conversation/message ids) is validated by the memory
# service against the actual rows — fabricated references are rejected.

MemoryKind = Literal["fact", "preference", "goal", "event", "person", "other"]
MemoryOrigin = Literal["user_stated", "inferred"]
MemoryStatus = Literal[
    "accepted", "review", "confirmed", "conflicted", "superseded", "orphaned", "deleted"
]


class MemoryCreateIn(BaseModel):
    """Creation input for a structured memory.

    Defaults (documented semantics):
    - status omitted: `accepted` for user-stated memories, `review` for
      inferences (uncertain material must not enter storage as fact).
    - confidence omitted: 1.0 for user-stated, 0.5 for inferred. Confidence
      expresses extraction certainty only; it is never proof of truth.
    - lifecycle statuses (conflicted/superseded/orphaned/deleted) cannot be
      set at creation; they are reached only via status transitions.
    """

    kind: MemoryKind
    statement: str = Field(min_length=1, max_length=MAX_MEMORY_STATEMENT_CHARS)
    subject: str | None = Field(default=None, max_length=MAX_MEMORY_SUBJECT_CHARS)
    keywords: list[str] = Field(default_factory=list, max_length=MAX_MEMORY_KEYWORDS)
    origin: MemoryOrigin
    status: Literal["accepted", "review", "confirmed"] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_conversation_id: str | None = None
    source_message_ids: list[str] = Field(
        default_factory=list, max_length=MAX_MEMORY_SOURCE_MESSAGE_REFS
    )

    @field_validator("statement")
    @classmethod
    def clean_statement(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Memory statement must not be empty")
        return stripped

    @field_validator("subject")
    @classmethod
    def clean_subject(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("keywords")
    @classmethod
    def clean_keywords(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for keyword in value:
            stripped = keyword.strip()
            if not stripped:
                continue
            if len(stripped) > MAX_MEMORY_KEYWORD_CHARS:
                raise ValueError(
                    f"Each keyword must be at most {MAX_MEMORY_KEYWORD_CHARS} characters"
                )
            if stripped.lower() not in (k.lower() for k in cleaned):
                cleaned.append(stripped)
        return cleaned


class MemoryCorrectionIn(BaseModel):
    """Partial correction of a memory's content fields.

    Provenance, kind, origin, status, and ids cannot be corrected through this
    model: changes to *what was said* or *when* are history, not metadata.
    Unknown fields are rejected rather than silently ignored, so a client
    cannot submit a lifecycle status, origin, confidence, or source reference
    through the correction endpoint. At least one field must be supplied
    (enforced by the service via fields-set).
    """

    model_config = ConfigDict(extra="forbid")

    statement: str | None = Field(default=None, min_length=1, max_length=MAX_MEMORY_STATEMENT_CHARS)
    subject: str | None = Field(default=None, max_length=MAX_MEMORY_SUBJECT_CHARS)
    keywords: list[str] | None = Field(default=None, max_length=MAX_MEMORY_KEYWORDS)

    @field_validator("statement")
    @classmethod
    def clean_statement(cls, value: str | None) -> str | None:
        if value is None:
            raise ValueError(
                "Statement must not be null; omit the field to keep the current statement"
            )
        stripped = value.strip()
        if not stripped:
            raise ValueError("Memory statement must not be empty")
        return stripped

    @field_validator("subject")
    @classmethod
    def clean_subject(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("keywords")
    @classmethod
    def clean_keywords(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            raise ValueError(
                "Keywords must not be null; send an empty list to clear them"
            )
        cleaned: list[str] = []
        for keyword in value:
            stripped = keyword.strip()
            if not stripped:
                continue
            if len(stripped) > MAX_MEMORY_KEYWORD_CHARS:
                raise ValueError(
                    f"Each keyword must be at most {MAX_MEMORY_KEYWORD_CHARS} characters"
                )
            if stripped.lower() not in (k.lower() for k in cleaned):
                cleaned.append(stripped)
        return cleaned


class MemoryStatusIn(BaseModel):
    """Explicit lifecycle status transition for a memory.

    All lifecycle statuses are reachable here (unlike creation), including
    conflicted/superseded/orphaned/deleted. Deleting sets `status='deleted'`
    and keeps the row so its history remains auditable.
    """

    status: MemoryStatus
    reason: str | None = Field(default=None, max_length=MAX_MEMORY_STATUS_REASON_CHARS)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class MemoryOut(BaseModel):
    """Stored memory record (JSON aggregates decoded for the caller)."""

    id: str
    kind: str
    statement: str
    subject: str | None
    keywords: list[str]
    origin: str
    status: str
    confidence: float
    source_conversation_id: str | None
    source_message_ids: list[str]
    created_at: str
    updated_at: str
    last_confirmed_at: str | None


class MemoryHistoryOut(BaseModel):
    """One history entry; `value` is the decoded JSON snapshot/metadata."""

    id: str
    memory_id: str
    event: str
    value: dict
    created_at: str


# ---------------------------------------------------------------------------
# Memory management API (Phase 2, Step 6).
#
# These models are the public memory-review contract. Provenance is returned
# only after validating it against the actual conversations/messages rows, so
# a stale or fabricated reference never appears as if it were usable. The raw
# stored columns (`source_conversation_id`, `source_message_ids`) are not part
# of the response contract.


class MemoryReviewIn(BaseModel):
    """Optional note attached to a confirm/reject decision.

    A client can never submit a target status: the endpoint decides the only
    allowed transition. Extra fields are rejected.
    """

    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=MAX_MEMORY_STATUS_REASON_CHARS)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class MemoryProvenanceOut(BaseModel):
    """Validated source references for one memory.

    `conversation_id` is present only if that conversation still exists;
    `message_ids` contains only ids that still exist and belong to the
    declared conversation. `valid` is False when a declared message is stale
    or mismatched even if the conversation survives, and for records with no
    usable source at all. Retrieval skips `valid == False` records.
    """

    conversation_id: str | None
    message_ids: list[str]
    valid: bool


class MemoryApiOut(BaseModel):
    """One memory as exposed by the management API (no raw source columns)."""

    id: str
    kind: str
    statement: str
    subject: str | None
    keywords: list[str]
    origin: str
    status: str
    confidence: float
    provenance: MemoryProvenanceOut
    created_at: str
    updated_at: str
    last_confirmed_at: str | None


class MemoryDetailOut(MemoryApiOut):
    """A single memory plus its (capped) chronological history."""

    history: list[MemoryHistoryOut]


class MemoryListOut(BaseModel):
    """One bounded page of memories plus the total for the same filters."""

    items: list[MemoryApiOut]
    total: int
    limit: int
    offset: int


# ------------------------------------------------------------ voice notes ---


class VoiceNoteSummaryOut(BaseModel):
    """List-row metadata. Transcript text is reduced to a short preview."""

    id: str
    title: str
    created_at: str
    updated_at: str
    audio_available: bool
    audio_deleted: bool
    audio_duration_seconds: float | None
    transcript_status: str
    transcript_source: str | None
    transcript_preview: str | None


class VoiceNoteOut(VoiceNoteSummaryOut):
    """One voice note, including its transcript and mistake metadata.

    `audio_available` is false when the file is missing or was deleted; the
    transcript is retained either way. No filesystem path is ever returned.
    """

    audio_content_type: str
    audio_bytes: int
    audio_deleted_at: str | None
    transcript: str | None
    machine_transcript: str | None
    transcript_error: str | None
    transcript_language: str | None
    transcript_updated_at: str | None


class VoiceNoteTranscribeIn(BaseModel):
    """Re-transcription request.

    `force` is required to replace an existing user correction; without it the
    API refuses with 409 rather than silently overwriting the user's wording.
    """

    force: bool = False


class VoiceNoteToChatOut(BaseModel):
    """Result of explicitly turning a transcript into a memory source."""

    conversation_id: str
    message_id: str


class VoiceNoteUpdateIn(BaseModel):
    """Rename and/or correct the transcript.

    A corrected transcript is stored with `transcript_source = 'user'`, while
    the last machine transcript remains available for comparison. Omitted
    fields keep their stored values; an all-null body is rejected.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=MAX_VOICE_NOTE_TITLE_CHARS)
    transcript: str | None = Field(default=None, max_length=MAX_TRANSCRIPT_CHARS)

    @field_validator("title")
    @classmethod
    def clean_voice_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("Title must not be blank")
        return stripped

    @field_validator("transcript")
    @classmethod
    def clean_transcript(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("Transcript must not be blank")
        return stripped

    @model_validator(mode="after")
    def at_least_one_field(self) -> "VoiceNoteUpdateIn":
        if self.title is None and self.transcript is None:
            raise ValueError("Provide a title or a transcript to update")
        return self
