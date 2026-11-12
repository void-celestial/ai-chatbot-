# Product Requirements Document (PRD)

## 1. Product overview
**Working name:** Personal AI Companion  
**Product type:** Single-user, local-first desktop-accessible web application  
**Primary user:** The owner using the app on their own Windows PC  
**Core model:** Qwen 3.5 9B served by Ollama  
**Primary value:** A conversational companion with reliable, inspectable long-term memory across sessions.

## 2. Problem statement
A standard chatbot often loses details when a conversation ends or context becomes too long. The user wants to return to old topics, refer to people or fictional characters introduced earlier, and have the assistant retrieve the correct context without repeatedly explaining it. The product must solve this through a durable archive and explicit retrieval pipeline.

## 3. Goals
- Support open-ended, natural conversation.
- Persist every user and assistant message by default in a local database.
- Retrieve relevant past messages and memories before generating an answer.
- Track entities, facts, relationships, and dated events.
- Keep fiction/story universes separate from real-life memory.
- Make memory inspectable and correctable by the user.
- Operate without external cloud APIs in the normal chat path.
- Remain usable on a Windows PC with 16 GB RAM and an RTX 4050; avoid unnecessary model reloads and excessive context.

## 4. Non-goals
- Guarantee perfect recall of every detail without retrieval errors.
- Replace human relationships or claim human-like feelings.
- Run arbitrary computer actions autonomously in v1.
- Require a paid API or external account.
- Train or fine-tune a model in v1.

## 5. Primary user stories
These stories describe the eventual product. The implementation-status note in §6
and the scope note in §8 identify which requirements are implemented now and
which are planned or deferred.

1. As a user, I can start a conversation and receive a natural response from my local model.
2. As a user, I can close and reopen the app without losing messages.
3. As a user, I can ask, “What was the name of the character I introduced last week?” and the app searches archived messages and saved memories.
4. As a user, I can create a story universe and store characters, traits, relationships, and events within it.
5. As a user, I can correct a wrong memory and see the corrected value used in future responses.
6. As a user, I can inspect the source conversation behind a recalled memory.
7. As a user, I can search old conversations by phrase, date, topic, or entity.
8. As a user, I can choose whether a memory is saved, edit it, or delete it.
9. As a user, I can see a clear error if Ollama is stopped or the selected model is missing.
10. As a user, I can export my conversations and memories to a local file.

## 6. Functional requirements

**Implementation status (2026-10-22):** this document is the product vision and
retains planned requirements. Phases 1–3 implement chat/persistence, the persona
API and prompt composition, structured memory storage, extraction/validation,
keyword retrieval, the memory management API, the review UI, conversation
deletion with explicit derived-memory handling, local JSON export, outdated-memory
marking, per-reply memory sources, and bounded follow-up retrieval. Phase 4
implements human-recorded voice notes: browser recording, local import/playback,
transcript storage and correction, and explicit use of a corrected transcript as
a memory source. Local transcription uses the installed ffmpeg whisper.cpp filter
and requires a user-installed GGML model; no model was downloaded here, so real
transcription remains unverified and Phase 4 is not complete. Story universes,
archive search, semantic retrieval, check-ins, continuity summaries, permanent
erase, in-app settings, and a source-aware debug panel are planned/deferred. Phase
2 provenance and correction guarantees remain enforced and regression-tested, and
Phase 3 acceptance evidence is recorded in `PROJECT_ROADMAP.md`.

### FR-1 Chat
- Send and display user/assistant messages.
- Persist each message before or as it is accepted into the conversation flow.
- Support streamed output when the local Ollama API supports it.
- Provide stop-generation and retry controls where technically supported.
- Show model name and Ollama connection status.
- Handle empty messages, API errors, timeouts, and model-not-found responses.

### FR-2 Conversations
- Create, rename, archive, and delete conversations. — Create/list/open, stable message ordering, and deletion are implemented; rename and archive are planned.
- Display conversations sorted by last activity. — Implemented.
- Load older messages with stable ordering and timestamps. — Implemented (current conversation load; no paged archive UI).
- Deleting a conversation must clearly explain whether linked extracted memories are also removed; default behavior should not silently delete unrelated memories. — Implemented: the conversation and its messages are deleted, derived memories are retained as `orphaned` (excluded from replies, history reason recorded), and the UI explains that backups/exports may retain copies.

### FR-3 Memory archive
- Store original messages as immutable source records unless the user deletes them. — Implemented for normal chat; message deletion is not exposed.
- Extract candidate memories asynchronously or in a separate processing step so chat is not blocked. — Implemented as an in-process background task.
- Each derived memory must include source message IDs, created/updated timestamps, confidence, memory type, and status. — Implemented. Every cited message must still exist and belong to the source conversation before the memory is used as grounded evidence.
- Deduplicate candidate memories; do not overwrite a conflicting fact silently. — Implemented conservatively (duplicates skipped, potential conflicts skipped rather than overwritten).
- Allow review, edit, pin, correction, and deletion. — Review/edit/correction/soft deletion are implemented; pinning is planned and not implemented.
- Distinguish explicit facts from inferred preferences and uncertain interpretations. — Implemented via `origin` and `status`, with provenance validation.

### FR-4 Retrieval
- Search structured memories and original conversation archive. — Structured-memory keyword retrieval is implemented; original-archive search is planned.
- Use lexical/keyword search in the initial version. — Implemented (FTS5 with LIKE fallback).
- Add local semantic embeddings only after baseline search works. — Planned for the semantic-retrieval phase; not implemented.
- Rank results by relevance, recency where appropriate, confidence, and entity/story scope. — Relevance/recency ranking is implemented; entity/story scope is not modeled.
- Pass only relevant excerpts into the model, not the entire archive. — Implemented with count and character budgets; memories with invalid provenance are excluded before formatting.
- Keep a retrieval trace for debugging: source IDs and short reasons, visible in a developer/debug panel. — Implemented for each reply: assistant messages store a bounded `memory_evidence` trace with memory ids, status, and validated source ids, shown in a disclosure under the reply. A dedicated developer/debug panel is still planned.

### FR-5 Story memory (planned; not implemented)
- Support named story universes.
- Store characters, aliases, attributes, relationships, events, and canon rules.
- Every story entity must belong to a universe.
- Preserve chronology and source references.
- Mark a fact as canon, tentative, contradicted, or superseded.
- Do not mix details from different universes unless the user explicitly asks for a crossover.

### FR-6 Personality and behavior
- Support a configurable system prompt/personality profile.
- Default behavior is warm, attentive, curious, nonjudgmental, and natural.
- Avoid forcing every conversation into a productivity task.
- Do not fabricate personal memories or claim certainty without evidence.
- Do not claim consciousness, human emotions, or exclusivity.
- Respect boundaries and user corrections.

### FR-7 Privacy and control
- Local-only normal operation. — Implemented.
- No telemetry or third-party API calls by default. — Implemented.
- Provide export and deletion controls. — Implemented: memory soft deletion, conversation deletion with orphaned derived memories, and a single-file JSON export of the documented scope (`GET /api/export`) with a sidebar action.
- Show the local data directory and backup instructions. — Migration backups exist and the README documents the data directory; the UI exposes export but still has no dedicated backup/restore screen.
- Do not log full private message contents to terminal logs by default. — Implemented (logs carry counts, ids, and reason codes).
- If semantic embeddings are enabled, generate them locally. — Planned; embeddings are not implemented.

### FR-8 Settings and diagnostics
- Configure installed Ollama model tag, response length, temperature, and memory options. — Model tag and key limits are environment-driven; an in-app settings screen is planned.
- Validate the model tag against locally available models where possible. — Health/model checks exist; tag validation at save time is planned.
- Show actionable error messages and a diagnostics screen. — Chat/memory error states exist; a dedicated diagnostics screen is planned.
- Provide database backup/restore instructions. — Migration backups exist; documented restore procedures are limited.

### FR-9 Voice notes (Phase 4; local transcription pending model install)
- In-app recording with explicit permission handling, plus local file import as a fallback. — Implemented (MediaRecorder feature detection; microphone access only on an explicit button press).
- Store recordings locally with generated filenames; persist metadata and relationships in SQLite. — Implemented (`VOICE_NOTES_DIR`, `voice_notes` table; no audio bytes in SQLite).
- Play recordings after restart and handle missing/deleted audio. — Implemented (FileResponse playback; 404/410 with explanatory UI).
- Transcribe locally with no cloud fallback. — Implemented integration via ffmpeg's whisper.cpp filter; not verified against a real model, and no model is bundled or downloaded.
- Show transcription status and clear failures, with retry. — Implemented (`pending`/`processing`/`completed`/`failed`, safe error codes, retry).
- Allow correction and preserve the machine transcript separately. — Implemented (`transcript` vs `machine_transcript`, `transcript_source`; retry requires explicit replacement of a correction).
- Delete audio independently from the transcript. — Implemented (audio deletion keeps the note/transcript and marks `audio_deleted_at`).
- Keep audio out of chat and memory automatically; use corrected transcripts downstream only by explicit user action. — Implemented (`Use transcript for memory…` creates one new conversation and runs normal extraction; no automatic conversion).
- No TTS, voice cloning, cloud transcription, or background listening. — Implemented as an explicit non-goal.

## 7. Non-functional requirements
- Reliability: all accepted messages persist across restart.
- Performance: show streaming output where supported; do not run memory extraction synchronously if it noticeably delays replies.
- Security: bind backend to localhost by default; validate inputs; restrict CORS to the local frontend; never expose arbitrary shell execution.
- Maintainability: typed frontend models, modular backend services, migrations/versioning for database schema.
- Accessibility: keyboard navigation, visible focus, readable contrast, reduced-motion respect.
- Resilience: recover gracefully from Ollama being unavailable or a malformed model response.

## 8. MVP acceptance criteria

**Scope note:** this list describes the eventual MVP, including later roadmap
phases. Phases 2 and 3 have been assessed against their acceptance criteria in
`PROJECT_ROADMAP.md`. Phase 4's recording/playback/correction/deletion behavior
is implemented and tested, but "transcription runs locally" is not verified until
a whisper.cpp model is installed. Story universes, archive message search,
semantic retrieval, check-ins, and the debug-panel items here are not yet
satisfied.

- A message survives app and backend restart.
- A conversation can be reopened with the correct message order.
- With a seeded test conversation, a query about a previously mentioned character retrieves the correct source record.
- The assistant does not claim to remember a detail when no supporting source is found.
- Two story universes with identically named characters remain separate.
- Correcting a memory changes future retrieval results and preserves audit/source metadata.
- Deleting a memory prevents it from being used in future prompts.
- Ollama-offline and missing-model states are displayed clearly.
- The frontend production build and backend tests pass.
- No required button is inert and no acceptance test is represented by fake data.
