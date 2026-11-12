# Architecture and Technical Design

## 1. High-level architecture

```text
React + TypeScript UI (Vite)
  | HTTP / streaming response
  v
FastAPI local backend (127.0.0.1)
  |-- Chat service
  |-- Prompt/context builder
  |-- Memory extraction service (implemented)
  |-- Retrieval service (implemented, keyword only)
  |-- Story/entity service (planned; not implemented)
  |-- Ollama client (implemented)
  |-- Export service (implemented; backups/restore remain migration snapshots)
  |-- Voice-note service (implemented; local audio files + transcripts)
  |-- Local transcription service (implemented; ffmpeg whisper.cpp filter,
      user-supplied model, no network call)
  v
SQLite database (implemented tables)
  |-- conversations
  |-- messages
  |-- persona
  |-- memories
  |-- memory_history
  |-- voice_notes
  |-- schema_migrations
  |-- memories_fts (FTS5 index, optional build)
Planned, not implemented: entities, story_universes, relationships, events,
message/entity links, memory_embeddings.

FastAPI --> Ollama at http://localhost:11434
            |-- Qwen chat model
            |-- optional local embedding model later

FastAPI --> local ffmpeg (whisper filter) --> user-installed GGML model
            (audio never leaves the machine)
```

## 2. Recommended repository layout

```text
personal-ai-companion/
  docs/
    PRD.md
    ARCHITECTURE.md
    AI_BEHAVIOR.md
    MEMORY_SPEC.md
    BUILD_PLAN.md
  frontend/
    src/
      components/        (chat + memory review + voice-note components)
      hooks/             (useCompanion, useMemories, useVoiceNotes, useTheme)
      api/               (typed fetch client)
      test/              (Vitest harness/setup)
      types.ts
      memoryUtils.ts
      styles.css
  backend/
    app/
      main.py
      config.py
      api/
      services/
        chat_service.py
        ollama_client.py
        prompt_builder.py
        persona_service.py        (implemented, Phase 2 Step 2)
        memory_service.py         (implemented, Phase 2 Step 3)
        memory_extraction.py      (implemented, Phase 2 Step 4)
        memory_retrieval.py       (implemented, Phase 2 Step 5)
        voice_notes.py            (implemented, Phase 4 storage/lifecycle)
        transcription.py          (implemented, Phase 4 local engine)
        story_service.py          (planned; not implemented yet)
      db/
        connection.py
        schema.sql
        migrations/
      models/
      tests/
    requirements.txt
  .env.example
  .gitignore
  README.md
```

Do not generate every file in one giant module. Keep responsibilities separated.

## 3. Data model (implemented schema)

Only `conversations`, `messages`, `persona`, `memories`, `memory_history`,
`voice_notes`, `schema_migrations`, and the FTS5 `memories_fts` index exist
today. The
`entities`, `story_universes`, `relationships`, `events`, and
`memory_embeddings` sections below are planned designs, not implemented
tables; they are not created by any migration.

### conversations
- id: UUID/text primary key
- title: text
- created_at: ISO-8601 UTC
- updated_at: ISO-8601 UTC
- status: active/archived

### messages
- id: UUID/text primary key
- conversation_id: foreign key
- role: system/user/assistant/tool
- content: text
- created_at: ISO-8601 UTC
- model_tag: nullable text
- generation_status: complete/interrupted/failed
- metadata_json: nullable JSON text

### memories (implemented in migration 0002)
- id: text primary key (`uuid4().hex`, 32 lowercase hex characters)
- kind: fact/preference/goal/event/person/other
- statement: text (the derived, user-reviewable statement)
- subject: nullable text
- keywords_json: JSON array of strings
- origin: user_stated/inferred
- status: accepted/review/confirmed/conflicted/superseded/orphaned/deleted
- confidence: real between 0 and 1 (extraction certainty, not proof of truth)
- source_conversation_id: nullable foreign key to conversations (`ON DELETE SET NULL`)
- source_message_ids_json: JSON array of source message IDs
- related_ids_json: nullable JSON (reserved; not used by the current services)
- created_at, updated_at: ISO-8601 UTC
- last_confirmed_at: nullable ISO-8601 UTC

### memory_history (implemented in migration 0002)
- id: text primary key
- memory_id: foreign key to memories (`ON DELETE CASCADE`)
- event: created/corrected/merged/conflicted (the current services emit
  created and corrected; status transitions are stored as corrected events
  with `status_from`/`status_to`/`reason`)
- value_json: JSON audit snapshot (source-reference keys are stripped by the API)
- created_at: ISO-8601 UTC

There is no `scope` or expiry column in the current schema; story-universe
scope and date-based expiry belong to later roadmap phases. Provenance is
validated at read time as described below.

### entities (planned; not implemented)
- id: UUID/text primary key
- name: text
- entity_type: person/fictional_character/place/organization/object/concept
- universe_id: nullable text
- aliases_json: JSON array
- attributes_json: JSON object
- status: active/archived

### story_universes (planned; not implemented)
- id: UUID/text primary key
- name: text
- description: text
- canon_notes: text
- created_at, updated_at

### relationships (planned; not implemented)
- id: UUID/text primary key
- universe_id: nullable text
- from_entity_id, to_entity_id: foreign keys
- relationship_type: text
- description: text
- source_message_ids_json: JSON array
- confidence: real
- status: active/tentative/contradicted/superseded

### events (planned; not implemented)
- id: UUID/text primary key
- universe_id: nullable text
- title: text
- event_date_text: nullable text
- sequence_index: nullable integer
- description: text
- entity_ids_json: JSON array
- source_message_ids_json: JSON array
- confidence: real
- status: active/tentative/contradicted/superseded

### memory_embeddings (planned; not implemented)
- Planned for the semantic-retrieval phase (Phase 7 in `PROJECT_ROADMAP.md`), not Phase 2.
- memory_id: foreign key
- embedding_model: text
- vector_blob or local vector-store reference
- created_at

No embeddings table or embedding generation exists today; keyword retrieval
over `memories_fts` with a LIKE fallback is the implemented baseline.

Use foreign keys and indexes. Store timestamps consistently in UTC; convert to local time only in the UI. If the user states a date without a timezone, preserve the original date text as well as any normalized value.

### voice_notes (Phase 4)
- id: UUID/text primary key (also the audio filename stem)
- title: user-visible label (never a path)
- audio_filename: generated `<id>.<ext>`; unique
- audio_content_type, audio_bytes, audio_duration_seconds
- audio_deleted_at: set when the audio is deleted but the transcript is kept
- machine_transcript: last machine output; transcript: current text
- transcript_source: machine/user; transcript_status: pending/processing/completed/failed
- transcript_error: safe reason code (never raw engine output/paths)
- transcript_language, transcript_updated_at, created_at, updated_at

Audio bytes are never stored in SQLite. Migration `0003_voice_notes.sql` adds
the table and indexes; no conversation or memory rows are created by recording,
import, or transcription.

## 4. Chat request lifecycle
1. UI sends a user message to the backend.
2. Backend validates input and stores the message.
3. Retrieval service identifies likely relevant memories, entities, and past message excerpts. (Implemented for stored memories, Phase 2 Step 5: deterministic keyword retrieval over `accepted`/`confirmed` records only, FTS5 with a LIKE fallback. Phase 3 adds one bounded retry with the previous user message's terms when the current message finds nothing; entities, story scope, and archive excerpts are still pending.)
4. Context builder assembles: behavior instructions, relevant memory evidence, recent conversation turns, and the new message. (Implemented: `prompt_builder.py` orders core rules → persona block/guardrail → a bounded, delimited memory evidence block → recent turns as separate messages. Memory text is data, never instructions, and has its own character budget.)
5. Ollama generates a response using the configured model tag.
6. Stream response to the UI if supported.
7. Persist final assistant response and generation status.
8. A separate memory task extracts candidate facts/events from the new exchange. (Implemented, Phase 2 Step 4: background task after the SSE response, newest exchange only, local Ollama JSON call, never blocking the reply.)
9. Validate/deduplicate candidates and save as reviewable memories; never let extraction overwrite a conflicting fact without preserving the conflict. (Implemented conservatively: model output is untrusted, provenance is derived from actual message records via verbatim quote anchoring, duplicates and potential conflicts are skipped rather than overwritten; semantic conflict detection is still open.)
10. UI can show the sources used in a memory/debug panel. (Implemented as a memory review screen, Phase 2 Step 7: list/detail inspection with validated provenance and a source-conversation link only when that conversation exists in the sidebar. Phase 3 adds a per-reply `memory_evidence` trace stored in `messages.metadata_json` and a disclosure under the assistant message. A fuller source-aware chat/debug panel is still not implemented.)

If generation fails after the user message is saved, retain the user message and show a retry option. Do not store an empty assistant message as a successful answer.

### Memory management API (Phase 2, Step 6)
`backend/app/api/memories.py` provides the review backend for stored memories:
- `GET /api/memories` — bounded list (`limit` 1–100, default 50, `offset`, `total`) filtered by the schema's `status`, `kind`, and `origin`; ordering is `updated_at DESC, id ASC` so pages are deterministic.
- `GET /api/memories/{id}` — memory fields, validated provenance (`conversation_id`/`message_ids` that still exist, with messages required to belong to the declared conversation, plus `valid`), and the newest 100 `memory_history` entries in chronological order. Audit snapshots are returned without raw source columns, which are represented only by the validated provenance block.
- `POST /api/memories/{id}/confirm` — `review`/`accepted` → `confirmed`; origin, confidence, and provenance are never upgraded.
- `POST /api/memories/{id}/reject` — only `review` → soft `deleted`, with the reason in the history. The schema has no `rejected` status, so rejection is a documented soft delete rather than a schema change.
- `POST /api/memories/{id}/supersede` — `accepted`/`confirmed` → `superseded`, with an optional reason in the history (Phase 3). The record, provenance, and history remain inspectable; retrieval and prompt evidence exclude it. Other transitions return 409.
- `PATCH /api/memories/{id}` — statement/subject/keywords only, for `accepted`/`review`/`confirmed`; explicit `statement: null` (and `keywords: null`) is rejected with 422 before any write, omitted fields keep their stored values, and the prior snapshot is appended to `memory_history` transactionally.
- `DELETE /api/memories/{id}` — idempotent soft delete (204); rows and history remain, retrieval excludes the memory immediately, and source conversations/messages are untouched.

IDs are validated as 32-character lowercase hex (`new_id()` = uuid4 hex); malformed ids are 422, unknown ids 404, disallowed transitions 409. Request models reject unknown fields, so clients cannot submit lifecycle statuses, origin, confidence, ids, or source references. Storage errors return a fixed safe 500 message. The existing localhost binding and CORS policy are preserved (DELETE was added to the allowed methods for the local frontend).

### Provenance policy (Phase 2 acceptance repair)
`memory_service.resolve_source_references` is the single policy used by both
the management API and retrieval:
- **Message-bearing records:** every declared source message must still exist;
  when a source conversation is declared, that conversation must also exist
  and every message must belong to it. If any declared message is missing or
  belongs to another conversation, `valid` is false and retrieval excludes the
  memory. Surviving ids are returned for display, but stale ids are never
  presented as verified evidence.
- **Legacy conversation-only records:** a record with no message-level
  references is valid while its declared conversation exists. This preserves
  records created before message-level provenance was required.
- **Message-only records:** valid when all declared messages exist; no current
  conversation is required, so a valid memory from conversation A remains
  retrievable while replying in conversation B.
- **No usable source:** invalid. The memory is not supplied to the model.

Creation still rejects unknown conversations/messages and messages from a
different declared conversation. The repair did not add a migration or
rewrite stored ids; deleted message ids remain in the audit row but are
filtered out of the API provenance and retrieval result.

### Memory review UI (Phase 2, Step 7)
The frontend consumes only the endpoints above; no backend behavior or schema was changed for the UI.
- Navigation: `App.tsx` holds a `chat | memories` view state. The existing `Sidebar` gains a `Memories` button with `aria-current`, and the chat pane is not mounted while the review screen is open. Opening a source conversation switches back to the chat view and selects that conversation through the existing hook.
- Components: `MemoryView` owns filters, counts, pagination, and the `Needs review` shortcut; `MemoryList` renders rows and page controls; `MemoryDetail` renders provenance, history, and the confirm/reject/edit/delete interactions; `MemoryStatusBadge` shares the lifecycle styling; `useMemories` owns fetching, selection, pagination, mutation guards, and refetch-after-mutation; `memoryUtils.ts` holds labels, history descriptions, and the client-side mirror of the API lifecycle policy (`review`/`accepted` confirmable, `review` rejectable, `accepted`/`review`/`confirmed` editable).
- API layer: `api/client.ts` exposes typed `listMemories`, `getMemory`, `confirmMemory`, `rejectMemory`, `correctMemory`, and `deleteMemory` functions built on the existing fetch/`ApiRequestError` path; `types.ts` mirrors the Pydantic list/detail/provenance/history payloads. No component calls `fetch` directly.
- State and actions: the list keeps the API's `total`/`limit`/`offset` (20 per page) and refetches with the active filters after every mutation; the detail is refetched after mutations and error responses so lifecycle controls cannot remain stale. Mutations set a single in-flight guard, disable the action controls, and show progress. Confirmation panels are in-app inline sections with an explanation, focus moved to `Cancel`, and Escape to dismiss; no `alert()`/`confirm()` is used.
- Truthfulness: rejected and manually deleted records both return the `deleted` status, so the UI labels both `Inactive` and only reports a rejection when the history contains the reason. Delete confirmation states that the record and history remain and that source conversations/messages are not deleted.
- Failure handling: loading, filtered-empty, un-filtered-empty, list error + retry, detail error + retry, inline field validation, pending states, 409/422/404 mutation errors, and network failures each have distinct UI. Source references are displayed as ids; a conversation link is shown only when the id is present in the loaded conversation list, so it always has a real destination. When `provenance.valid` is false — including a deleted source message with a surviving conversation — the list shows `No valid source`, the detail shows the invalid-source warning, and the memory is excluded from replies.
- Tests: Vitest + jsdom (dev-only). `npm test` covers the API layer, list/filter/pagination, loading/empty/error/retry, detail/provenance, confirm/reject (including the optional reason and 409), edit validation, duplicate-submission guarding, delete confirmation copy, and view navigation; `App.test.tsx` covers the sidebar entry and returning to chat. Styling reuses the existing CSS variables, responsive layout, reduced-motion rules, and light/dark themes.
- Limitations: no source message previews, no developer/debug panel, no deep links/bookmarks to a memory, no permanent erase (export exists but is a current-state snapshot), and pages are ordered by `updated_at` (the API's deterministic order).

### Conversation deletion and export (Phase 3)
- `DELETE /api/conversations/{id}` (204, 404 when missing) deletes the conversation row and its messages inside one transaction after moving derived memories to `orphaned` with the history reason “Source conversation deleted”. Memories already `deleted`, `superseded`, or `conflicted` are left untouched. The response carries no body; the UI states that records/history are retained and that backups or previously downloaded exports can still contain copies.
- `GET /api/export` returns one JSON document (`format: companion-export`, version 1) with persona, conversations, messages (including `metadata`), every memory regardless of status, and memory history, plus `counts` and a `scope` block that names exclusions (FTS index, `data/backups/`, logs, previous exports). It is read-only, local-only, and does not claim erasure.
- The assistant reply persists `metadata.memory_evidence` (bounded: id, kind, statement, origin, status, source conversation/message ids) using the existing `messages.metadata_json` column; no migration was needed. Retrieval's follow-up retry uses only the previous user message from the same conversation and is recorded as `query_expanded` in logs.

### Voice-note API and lifecycle (Phase 4)
- `GET /api/voice-notes?limit=&offset=` — summaries newest first (max 100).
- `POST /api/voice-notes?title=` — raw audio body plus an allowlisted audio `Content-Type`. The server chooses the extension and generated filename, streams to a `.part` file with a 25 MB cap (`VOICE_NOTE_MAX_BYTES`), rejects empty/oversize/unsupported/undecodable input, then inserts metadata. No multipart dependency is used.
- `GET /api/voice-notes/{id}` — detail with transcript, machine transcript, status/error code, and `audio_available`/`audio_deleted`; no filesystem path is returned.
- `GET /api/voice-notes/{id}/audio` — inline `FileResponse` with the stored content type; 404 when the file is missing, 410 when audio was explicitly deleted.
- `PATCH /api/voice-notes/{id}` — title and/or transcript. A transcript edit becomes `transcript_source=user`, keeps the machine transcript, and is refused with 409 while a transcription is running.
- `POST /api/voice-notes/{id}/transcribe` — checks engine availability first (503 with an actionable message), atomically claims the note (`processing`), and schedules an in-process background task. Refuses concurrent runs and refuses to replace a user correction unless `{force:true}`. Success stores the transcript; failure stores a safe code and keeps audio/previous text.
- `POST /api/voice-notes/{id}/send-to-chat` — explicit memory integration: creates a new conversation with the current (corrected) transcript as a user message and schedules the normal extraction task. No assistant reply is generated; nothing is automatic.
- `DELETE /api/voice-notes/{id}/audio` — removes the file and marks `audio_deleted_at`, keeping transcript/metadata. `DELETE /api/voice-notes/{id}` removes the row and file.

Transcription engine: `backend/app/services/transcription.py` invokes the installed `ffmpeg` with the `whisper` filter over the local audio file, writing a temporary JSON result that is parsed defensively (`transcription` segments, `text`, `result.language`). The engine runs with a working directory such that the model and output are addressed relatively, avoiding filtergraph drive-colon escaping. CPU is the default (`WHISPER_USE_GPU=false`) to avoid competing with Ollama. Timeouts, missing binary/model, invalid output, empty speech, and non-zero exits map to distinct codes. A background task never raises into the API; startup marks rows left in `processing` by a restart as failed (`interrupted_by_restart`).

Storage and containment: filenames must match `^[0-9a-f]{32}\.[a-z0-9]{2,5}$`, and the resolved path must have the configured voice-notes directory as its parent. Uploads are written as `.<name>.part` and atomically renamed; failures remove the partial file. `ffprobe` is used best-effort for duration and format validation; if it is unavailable, uploads are not rejected on that basis.

Limitations: transcription requires a model that this project does not download; until one is installed the endpoint returns 503. Audio/transcript deletion is not complete erasure (backups and prior exports may retain copies). No TTS, voice cloning, cloud transcription, or background listening exists. The voice-note screen is a single view without deep links. Real transcription latency/quality on this machine is unmeasured because no model was installed.

## 5. Retrieval strategy
Phase 2 (keyword retrieval; implemented):
- SQLite full-text search or parameterized keyword search. (Implemented for memory records, Phase 2 Step 5: `memory_retrieval.py` uses the `memories_fts` FTS5 index when present and a parameterized LIKE scan otherwise. Ranking is deterministic token overlap with keyword/subject/statement weights; results are limited by a count limit and a character budget for prompt evidence, and only `accepted`/`confirmed` records with valid provenance are returned.)
- Search entities, memory summaries, aliases, and original messages. (Only memory records are searched so far; entities, aliases, and archive message search are pending.)
- Use conversation/story scope filters. (Not implemented yet; story universes do not exist in the schema.)
- Return a small ranked evidence set with source IDs. (Implemented, including source message ids in the retrieval result and the prompt record line. Phase 3 also stores the evidence set per assistant reply; the developer/debug panel is pending.)

Phase 7 (semantic retrieval; planned, not implemented):
- Add local embeddings through Ollama only after approval.
- Use a local vector index/library only after dependency compatibility is verified.
- Combine lexical and semantic rankings.
- Keep citations/source IDs attached to all results.
- Provide a fallback to lexical search if embeddings are missing or fail.

Keyword retrieval is the current baseline and fallback; no embedding model or
vector store is installed or used.

Never send the full archive to the model. Apply a context budget and rank evidence.

## 6. Ollama integration
- Read the model tag from config; default only to a value confirmed by `ollama list`.
- Check Ollama availability with a health request.
- Use the Ollama chat API at localhost; support streaming where available.
- Handle connection refused, timeout, model-not-found, malformed response, and cancellation.
- Do not assume that a model tag is exactly `qwen3.5:9b`; confirm the installed tag.
- Keep model-specific options configurable and documented.
- Do not call external model providers as fallback.

## 7. Security and privacy
- Backend listens on 127.0.0.1 by default.
- Restrict CORS to the local development/served frontend.
- Never add arbitrary terminal or shell execution to the MVP.
- Parameterize SQL queries.
- Validate payload sizes and types.
- Keep `.env`, databases, exports, logs, and backups out of Git unless explicitly intended.
- Do not send messages to analytics or cloud services.
- Include explicit export/delete actions and backup guidance.

## 8. Testing
- Unit-test date handling, memory deduplication, scope isolation, ranking, and prompt assembly.
- Integration-test Ollama failure states using a mocked HTTP client.
- Test database persistence across app restart.
- Test that deleted/corrected memories no longer influence prompt context.
- Test that fiction universes are isolated.
- Test frontend loading, error, empty, and streaming states.
