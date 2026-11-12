# DIGITAL COMPANION — MASTER PROJECT ROADMAP

**Document purpose:** Permanent source of truth for the project's vision, complete development phases, implementation order, quality requirements, and final destination.

**Project type:** Local-first personal AI companion.

**Current milestone:** Phase 4 — Human-recorded voice notes is implemented and tested, but its transcription acceptance criterion is not verified because no local whisper.cpp model is installed and downloading one requires explicit approval (2026-10-22). Phase 5 has not started.

**Documented progress as of 2026-10-22:**
- Phase 1 — Local chat foundation: implemented and tested.
- Phase 2, Step 1 — Database migration mechanism and initial memory/persona schema: implemented; reported passing 29 backend tests.
- Phase 2, Step 2 — Persona service, API, and prompt-builder refactor: implemented.
- Phase 2, Step 3 — Structured memory storage and tests: implemented; reported 97 backend tests passing.
- Phase 2, Step 4 — Memory extraction and validation: implemented and documented at this checkpoint; reported backend suite at that checkpoint: 131 passed, 0 failed. Extraction runs as an in-process background task after the chat response, uses the existing local Ollama client, and validates candidates in application code before persisting through the existing memory service. Provenance is derived from actual message records rather than trusting model-supplied IDs. (Retrieval, prompt injection, memory API endpoints, and review UI were not part of Step 4.)
- Phase 2, Step 5 — Memory retrieval and prompt integration: implemented and documented at this checkpoint; reported backend suite: 179 passed, 0 failed (131 before this step + 48 new). Local keyword retrieval over stored memories (SQLite FTS5, with a parameterized LIKE fallback when FTS5 or its index is unavailable), deterministic token-overlap ranking, only `accepted`/`confirmed` memories, provenance re-validated against real conversation/message rows, and configurable result/content budgets. Retrieved records enter the prompt only as a clearly delimited, untrusted evidence block placed after the core behavior rules and persona settings. Not implemented in this step: memory API endpoints, review UI, embeddings/semantic search, archive search, entity/story scope.
- Phase 2, Step 6 — Memory management API: implemented and documented at this checkpoint; reported backend suite: 228 passed, 0 failed (179 before this step + 49 new). `GET /api/memories` (status/kind/origin filters, bounded pagination), `GET /api/memories/{id}` (validated provenance + capped history), `POST .../confirm` (`review`/`accepted` → `confirmed`, origin unchanged), `POST .../reject` (`review` → soft `deleted` with a recorded reason), `PATCH` (statement/subject/keywords with audit history), and `DELETE` (idempotent soft delete). Deleted/rejected memories are excluded from FTS5 and fallback retrieval; source conversations/messages are never deleted; no migration was needed. Not implemented in this step: memory review UI, archive search, entity/story scope, embeddings.
- Phase 2, Step 7 — Memory review UI: implemented and documented at this checkpoint; backend suite unchanged at 228 passed, 0 failed; frontend production build/type-check, lint (0 errors), and 35 Vitest/jsdom tests pass. A sidebar `Memories` entry opens a review screen backed only by the existing memory API: a list with status/kind/origin filters and 20-per-page pagination using the API's real `total`, a detail view with origin/confidence/timestamps/validated provenance and capped history, and confirm, reject (optional reason), correct (statement/subject/keywords), and confirmed soft-delete actions. A source conversation is linked only when its id is in the loaded sidebar list; source message ids are shown as identifiers, never as invented previews. Actions mirror the API lifecycle policy (confirm only `review`/`accepted`, reject only `review`, edit only `accepted`/`review`/`confirmed`); 409/422/404/network failures are surfaced without false success. Rejection and deletion both appear as `Inactive` because the backend shares the `deleted` status, and soft deletion is described without any permanent-erasure claim. Not implemented in this step: source-aware debug panel, archive message search, entity/story scope, embeddings.
- Phase 2 acceptance repair (2026-10-21): implemented and verified. Message-level provenance now requires every declared source message to exist and belong to the declared source conversation; a deleted or mismatched message makes the memory ineligible as grounded evidence and it is excluded before prompt formatting, while valid cross-conversation retrieval is preserved. `PATCH /api/memories/{id}` rejects an explicit `null` statement (and `null` keywords) with 422 before any write, leaving the record and history unchanged. Reported backend suite: 239 passed, 0 failed; frontend suite: 36 passed across 3 files; production build passed; lint reported 0 errors and 4 advisory warnings. No migration was added and no browser E2E was rerun for this repair.
- Phase 2, Step 8 — final acceptance audit (2026-10-22): completed and recorded. Independently re-run on the current checkout: backend suite 239 passed, 0 failed (`py -m pytest backend/app/tests --tb=short`); frontend suite 36 passed across 3 files (`npm test`); frontend production build passed (`npm run build`); lint 0 errors with 4 advisory React Compiler warnings (`npm run lint`). Disposable-database runtime checks through the real FastAPI app confirmed fresh and existing-database startup (v2, backup snapshot, idempotent re-run), persona persistence across restart, cross-conversation retrieval, deletion of a source message (retrieval exclusion, invalid API provenance, prompt exclusion, cross-conversation validity, mismatched message, and all-or-nothing multi-message provenance), and explicit-null corrections (422 with no record/history mutation; `subject: null` clears the subject; valid corrections change future retrieval, including after an application restart). Documentation was checked against the code and small status/accuracy corrections were made (`README.md`, `docs/MEMORY_SPEC.md`, `docs/BUILD_PLAN.md`). No migration, schema change, dependency, feature, or test change was made. Browser E2E was not rerun: the project has no browser automation framework or driver, so the memory review screen remains covered by Vitest and the Step 7 browser verification (documented in `docs/MEMORY_SPEC.md` §10).
- Phase 3 — Cross-session continuity + companionship polish (2026-10-22): steps 1–6 implemented and verified. `DELETE /api/conversations/{id}` removes the conversation and its messages while derived memories become `orphaned` (retained, excluded from retrieval, reason recorded); `GET /api/export` returns the documented local scope (persona, all conversations/messages, all memories including inactive ones, memory history) and excludes the FTS index/backups; `POST /api/memories/{id}/supersede` retires accepted/confirmed memories with a reason; assistant replies store a bounded `memory_evidence` trace in `messages.metadata_json` and the chat UI shows it in a disclosure; and retrieval retries with the previous user message's terms when the current message finds nothing (pronoun follow-ups). Backend suite: 275 passed, 0 failed; frontend: 51 tests in 4 files, production build and lint pass. Runtime checks on a disposable database through the real FastAPI app confirmed cross-conversation recall, per-reply evidence metadata, supersede/409, conversation deletion/orphaning, export counts and post-deletion state, and restart persistence; live retrieval also confirmed the follow-up retry. Live extraction against `qwen3.5:9b` measured 165–400s, so the default extraction timeout was raised from 90s to 600s (the previous default silently saved nothing). No migration, schema change, or new dependency was added. Not implemented: archive message search, continuity summaries, permanent erase.
- Phase 4 — Human-recorded voice notes (2026-10-22): recording/import/playback, metadata migration `0003_voice_notes.sql`, local transcript storage and correction, explicit "Use transcript for memory" integration, and audio-only deletion are implemented. Transcription is wired to the already-installed ffmpeg `whisper` filter (whisper.cpp) with a user-supplied GGML model; no Python package, cloud service, TTS, voice cloning, or background listening was added. Backend suite 318 passed, 0 failed; frontend 70 tests in 5 files; build passes; lint 0 errors and 5 advisory warnings. Disposable-database runtime checks confirmed upload/playback bytes, persistence across restart, correction, audio deletion (410, transcript kept), note deletion, and explicit transcript-to-conversation extraction. **Blocked for acceptance:** no whisper.cpp model is installed; `POST .../transcribe` returns 503 `model_missing`, so real transcription quality/latency and "no external network call in the transcription path" were not verified end-to-end. Downloading a model requires user approval; do not mark Phase 4 complete until then.
- Later phases: planned, not implemented unless subsequent evidence confirms otherwise.

**Important:** This document describes the intended destination and process. It does not claim that future features already exist.

---

# 1. THE PRODUCT WE ARE BUILDING

## 1.1 Vision

Build a personal digital companion that feels like a long-distance friend: familiar, attentive, consistent, capable of remembering meaningful experiences, and able to continue conversations naturally over time.

The experience should center on messaging. The companion should have a recognizable personality and persistent memory, understand relevant personal context, and eventually support human-recorded voice notes and considerate, user-controlled check-ins.

The goal is not merely to build a chatbot with a larger context window. The goal is to create a coherent, evolving companion experience whose continuity comes from trustworthy memory, retrieval, stable behavior, and thoughtful interaction.

The companion must always remain honest about being AI. It must never pretend that an app-generated message was a spontaneous human action or claim human feelings, experiences, or intentions it does not possess.

## 1.2 Core product principles

1. **Continuity:** Meaningful information can survive across conversations and application restarts.
2. **Trustworthy memory:** Stored information has traceable sources and clear uncertainty.
3. **Stable personality:** Identity, tone, and behavior do not depend on the availability of personal memories.
4. **Natural interaction:** Relevant memories are used naturally, not forced into every response.
5. **Honesty:** The companion admits uncertainty and does not fabricate recollections or experiences.
6. **Privacy:** Personal data stays local unless an explicitly approved feature requires otherwise.
7. **User control:** The user can inspect, correct, and delete personal memories.
8. **Simplicity:** The interface remains familiar, messaging-focused, and uncluttered.
9. **Reliability:** Every major feature has automated tests and demonstrable acceptance criteria.
10. **Incremental development:** Implement one authorized step at a time and verify it before proceeding.
11. **No artificial dependency:** The companion must not use guilt, possessiveness, fabricated distress, or manipulative notification tactics to encourage engagement.
12. **Truthful initiative:** Optional check-ins must be transparently attributed to the application's logic and user-configured preferences.

## 1.3 What this product is not

- It is not a self-serve SaaS platform.
- It is not a generic chatbot dashboard.
- It is not a system that automatically treats every conversation as permanent memory.
- It is not a human impersonation system.
- It is not allowed to invent shared experiences or claim it remembers information that cannot be supported.
- It must not introduce cloud services, telemetry, paid APIs, additional models, or background processes without appropriate approval.

---

# 2. EXISTING TECHNICAL FOUNDATION

The verified Phase 1 stack is:

- **Frontend:** React, TypeScript, Vite.
- **Backend:** FastAPI and Python 3.14.
- **Database:** SQLite.
- **Local language model:** Ollama with `qwen3.5:9b`.
- **Communication:** Streaming responses between the frontend and backend.
- **Development environment:** Windows, with the application bound to localhost.

Phase 1 established persistent conversations and messages, streamed chat, recent-turn context budgeting, health diagnostics, error handling, and the existing UI theme system.

The existing application must be inspected before making assumptions about filenames, schemas, functions, APIs, or current feature status.

The current model is a text model, not the audio transcription engine for future voice notes. Voice transcription will require a separate, locally running speech-recognition solution if approved in Phase 4.

The current recent-turn context window is not durable personal memory. A conversation being saved in SQLite does not mean the model can automatically recall its contents in a different conversation.

---

# 3. COMPLETE PHASE ROADMAP

## PHASE 1 — LOCAL CHAT FOUNDATION

**Status:** Implemented and tested according to the Phase 1 report.

### Objective

Create a reliable local application that can chat with the local model, stream responses, and persist conversations.

### Scope

- React + TypeScript + Vite frontend.
- FastAPI backend.
- SQLite conversation and message persistence.
- Ollama integration with `qwen3.5:9b`.
- Streaming chat responses.
- Recent-turn context budgeting.
- Health and model availability diagnostics.
- Error handling and retry behavior.
- Basic conversation UI and light/dark themes.
- Localhost-only operation.
- Automated backend tests and frontend production build.

### Acceptance criteria

- Messages stream correctly from the local model.
- Conversations and messages persist after restart.
- Failed generations and unavailable models produce honest error states.
- The frontend builds successfully.
- Backend tests pass.
- No external telemetry or cloud dependency is introduced.

### Completion rule

Preserve Phase 1 functionality throughout every subsequent phase. Any regression must be fixed before the affected phase is considered complete.

---

## PHASE 2 — DURABLE MEMORY FOUNDATION + PERSONA

**Status:** Steps 1–7 implemented and documented (schema/migration, persona, structured memory storage, extraction and validation, keyword retrieval and prompt integration, memory management API, memory review UI), and the 2026-10-21 acceptance repairs are implemented and verified. Documented Step 4 limitations: verbatim quote anchoring, heuristic contradiction detection, conservative sensitive-data filtering, potential-conflict skips instead of semantic conflict detection, newest-exchange-only processing, and possible loss of an in-process extraction task if the application exits. Documented Step 5 limitations: retrieval is lexical only (no semantic/embedding relevance), morphological normalization is limited to simple plurals, `review` memories are deliberately excluded from prompt evidence, the evidence block can be truncated by its budget, and retrieval quality depends on stored keywords/statements rather than on archive excerpts. Documented Step 6 limitations: deletion is a soft delete (row, history, and any backups remain; no permanent-erasure claim), rejection reuses the `deleted` status with the reason preserved in `memory_history` because the schema has no `rejected` status, corrections are limited to accepted/review/confirmed records, list pagination is capped, and detail history is capped. Documented Step 7 limitations: the UI shows source identifiers rather than message content or previews, source-conversation navigation works only when the conversation is present in the loaded sidebar list, rejection and manual deletion remain indistinguishable except through the recorded history reason, there is no permanent-erasure/export control, and the memory screen is a single view without deep links. Archive search, entity/story scope, and embeddings have not been implemented yet. The provenance and correction acceptance defects are fixed (see `MEMORY_SPEC.md` §11), and the Phase 2 acceptance criteria are satisfied. The Step 8 final acceptance audit (2026-10-22) re-verified each acceptance criterion against the current code, tests, and disposable-database runtime behavior; its evidence is recorded in the progress list above. Phase 2 is closed. Phase 3 is implemented and documented above. Phase 4 is implemented and tested, with real transcription blocked on an unapproved model download (see the Phase 4 section); Phase 5 has not started.

### Objective

Give the companion persistent personal memory, a stable configurable personality, and the foundations for retrieval across conversations.

### A. Persistent persona

- Persist the companion's name, tone, formality, and optional additional instructions.
- Provide validated backend endpoints for reading and updating settings.
- Separate stable behavior instructions from persona settings.
- Ensure persona settings survive application restarts.
- Prevent user-configurable instructions from overriding core safety and memory-trust rules.

### B. Structured memory

Create a system that can extract useful candidate memories from conversations using the existing local model.

Potential memory categories include:

- User preferences.
- Important personal facts.
- Long-term goals.
- Significant events.
- Relevant people and relationships.
- Other explicitly supported personal information.

Each memory must have a stable ID, source references, status, timestamps, and appropriate uncertainty metadata.

Distinguish:

- Explicitly stated information.
- Inferred information.
- Information awaiting review.
- Confirmed or corrected information.
- Conflicting information.
- Superseded information.
- Memories whose source has been deleted.

Do not automatically store every message. Avoid converting jokes, hypotheticals, fictional content, temporary statements, or unsupported model interpretations into permanent personal facts.

### C. Extraction and validation

- Use structured candidate output.
- Validate model output defensively.
- Verify source references against actual stored messages.
- Reject unsupported attribution.
- Deduplicate equivalent memories.
- Detect contradictions instead of silently overwriting information.
- Keep uncertain inferences pending review.
- Ensure extraction failure does not break ordinary chat.

### D. Memory review UI

Provide a simple interface that allows the user to:

- Inspect saved memories.
- See their origin and review status.
- Correct inaccurate information.
- Confirm or reject inferred information.
- Delete individual memories.
- Navigate to the source conversation when available.

Keep the interface consistent with the existing messaging application.

### E. Keyword retrieval

- Implement local keyword-based retrieval using SQLite FTS5 where available.
- Provide a safe fallback if FTS5 is unavailable.
- Retrieve only relevant memories.
- Limit the number and size of memories injected into the model context.
- Preserve source references for every retrieved item.
- Record which memories were actually supplied to a response.

### F. Memory trust requirements

- Never fabricate a memory.
- Never treat a model-generated confidence score as proof of truth.
- Distinguish evidence from interpretation.
- Respect corrections.
- Exclude deleted, superseded, unresolved-conflict, and unsupported orphaned memories from ordinary retrieval.
- If no relevant evidence exists, allow the companion to admit it does not remember.

### G. Deletion requirements

- Memory deletion must immediately exclude the memory from retrieval.
- Delete associated index entries and retained memory history according to the deletion policy.
- Define how derived memories behave when their source conversation or message is deleted.
- Do not claim complete erasure when persistent copies remain in backups or other storage.
- Document backup retention and deletion limitations.

### Acceptance criteria

- Persona settings persist across restart.
- A fact from conversation A can be retrieved in conversation B.
- Every retrieved memory has a valid source reference.
- Corrections remain authoritative after restart.
- Deleted memories are not retrieved.
- Source deletion cannot leave unsupported memories silently active.
- Memory extraction or retrieval failures do not break ordinary chat.
- All Phase 1 tests remain green.

### Completion rule

Do not start Phase 3 until persona, memory storage, retrieval, source tracking, deletion behavior, and their tests have been implemented and verified.

---

## PHASE 3 — CROSS-SESSION CONTINUITY + COMPANIONSHIP POLISH

**Status:** Implemented and verified (2026-10-22). Steps 1–6 of the work plan are complete; see the progress list above. Known limitations: deletion is not permanent erasure (backups and previously downloaded exports can retain copies), export is an on-demand JSON snapshot rather than a scheduled backup, `superseded` records remain listable for audit, archive message search and continuity summaries are deliberately deferred, and extraction on the reference machine is slow enough that memories appear minutes after the exchange.

### Objective

Make the companion feel continuous across days, weeks, and different conversations without forcing the user to repeat meaningful context.

### Scope

- Improve retrieval-driven cross-session recall.
- Surface relevant memory sources when appropriate.
- Provide continuity summaries when reopening conversations, if testing demonstrates that summaries help.
- Handle outdated memories and changing personal circumstances.
- Improve the relationship between conversation history, durable memories, and source references.
- Implement conversation deletion with explicit handling of derived memories.
- Add data export.
- Improve privacy controls and explain deletion consequences.
- Consider archive search if it provides meaningful value to the core experience.

Generic rename and archive features are lower priority than memory integrity, source transparency, deletion, and export.

### Continuity behavior

The companion should retrieve relevant information when a conversation resumes, but it should not automatically replay a long personal history.

A memory should be mentioned only when it helps the current conversation. The companion should not imply it remembers an event if retrieval did not provide adequate supporting evidence.

Implementation note (Phase 3): retrieval retries once with the previous user message's terms when the current message finds no evidence, which covers pronoun-only follow-ups such as “Where does she live?” after a named subject. The retry never crosses conversations, never bypasses lifecycle/provenance/budget filters, and is logged as expanded without exposing text. Assistant messages record which memories were supplied in `metadata_json.memory_evidence`, and the chat UI can show that disclosure under the reply.

### Acceptance criteria

- Relevant information from older conversations is retrieved when appropriate.
- Source information is accessible to the user.
- Irrelevant memories do not dominate ordinary replies.
- Deleting a conversation has explicit, tested effects on derived memories.
- Exported data accurately reflects the documented export scope.
- Continuity features do not break normal chat or the existing UI.

### Completion rule

Do not start Phase 4 until continuity behavior and deletion semantics have been tested, or explicitly document and approve any deliberate deferral that does not undermine the safety of voice-note storage.

---

# PHASE 4 — COMPLETE MODEL INSTALLATION AND ACCEPTANCE

**Status:** Implemented and tested, with one blocked acceptance item (2026-10-22). Recording/import/playback, local storage, transcript review/correction, explicit memory-source integration, and deletion semantics are complete. Local transcription uses the already-installed ffmpeg whisper filter (whisper.cpp) with a configurable GGML model; **no model is installed and none was downloaded**, so real transcription is unverified. The previous sentence in this section about downloading remains in force: obtain approval before installing a model.

Implementation notes: audio lives under VOICE_NOTES_DIR (default data/voice_notes/) as generated <id>.<ext> files; SQLite stores metadata/transcripts only (migration 0003_voice_notes.sql). Uploads are size/type validated and streamed, playback survives restart, audio can be deleted independently of the transcript, and deleting a note removes its row/file. The last machine transcript is kept separately from user corrections; re-transcription refuses to replace a correction without explicit confirmation, and an interrupted in-process job is marked failed after restart. Transcription runs on CPU by default (configurable) so it does not compete with Ollama. "Use transcript for memory" creates one new conversation from the current corrected text and runs the existing extraction rules; nothing is automatic. No TTS, voice cloning, cloud transcription, or background microphone listening exists.

### Objective

Allow the user to record personal voice notes that the companion can access, transcribe locally, reference, and use as memory sources.

These are human-recorded voice notes. This phase does not introduce AI-generated speech.

### A. Recording and playback

- Provide in-app microphone recording using supported browser audio capabilities.
- Explain microphone permission requirements and handle denial gracefully.
- Save recordings locally under the application's data directory.
- Persist database references to audio files.
- Support playback after application restart.
- Allow audio files to be deleted independently from transcripts where appropriate.

### B. Local transcription

Investigate a local transcription engine such as whisper.cpp or faster-whisper.

Before selecting an engine:

1. Validate compatibility with the project's Python version and Windows environment.
2. Measure transcription quality and latency.
3. Assess CPU and GPU memory usage alongside Ollama.
4. Determine supported recording formats and whether conversion is required.
5. Confirm that the transcription path can operate without external network calls.
6. Present the proposed engine, model, download size, licensing considerations, and hardware impact for approval.

**Do not download the transcription engine or model until the user approves the selected approach.**

### C. Transcript correction

- Display generated transcripts.
- Allow the user to correct recognition errors.
- Treat user corrections as authoritative.
- Preserve a clear distinction between raw recognition output and the corrected transcript where needed.
- Ensure downstream memory extraction uses the corrected text.
- Keep source references linking transcripts, recordings, and extracted memories.

### D. Audio and transcript privacy

- Store audio files locally.
- Handle missing, corrupted, or inaccessible recordings gracefully.
- Make it clear when deleting audio leaves a transcript behind.
- Make it clear when deleting a transcript affects derived memories.
- Do not imply audio has been erased while retained copies still exist.

### Acceptance criteria

- A recording can be played after restarting the application.
- Transcription runs locally using the approved engine.
- Missing microphone permission and transcription failures show clear errors.
- Corrected transcripts are used by downstream memory extraction.
- Audio and transcript deletion follow explicit, tested semantics.
- No TTS or external transcription service is introduced. — Verified by implementation and dependency review (ffmpeg/whisper.cpp only).
- No external network call occurs in the transcription path. — Implementation has no network client and runs only ffmpeg locally; not yet observed end-to-end with a real model because no model is installed.
- A recording can be played after restarting the application. — Verified on a disposable database.
- Corrected transcripts are used by downstream memory extraction. — Verified through the explicit "Use transcript for memory" action; nothing is sent automatically.
- Missing microphone permission and transcription failures show clear errors. — Covered by frontend tests and the 503/model failure path; real transcription failure timing is unverified without a model.I have a finished project that runs a local LLM. The code works already.
I need it packaged as a ready-to-run desktop app without changing how the
model logic works.

Project details:
- Language/framework: [Python / Node / other]
- How I run it now: [e.g. python main.py, or a Jupyter notebook]
- Local model runtime: [Ollama / llama.cpp / Hugging Face transformers / other]
- OS: Windows

What I need:
1. Read my project files first and tell me briefly what you found.
2. If it only has a terminal or notebook interface, add a simple, clean
   chat UI (use Gradio or Streamlit for Python, or Electron for Node,
   whichever fits best) with: a message box, a chat history, a loading
   indicator while the model responds, and a clear/reset button.
3. Make sure the model loads once at startup, not on every message, and
   handle errors (model not found, out of memory) with readable messages
   instead of crashes.
4. Package it into a double-click app for Windows (for example a .exe
   using PyInstaller, or an installer for Electron). Include the model
   path handling so it still finds the model file after packaging.
5. Create a requirements.txt (or package.json) and a README.md with exact
   steps to run from source AND to build the app.
6. Give me the exact commands to run, in order, and test them. If
   something fails, fix it and explain what changed.

Constraints:
- Keep it simple. Don't rewrite my existing logic.
- Everything must work offline.
- Tell me the final file size and where the finished app ends up.

### Completion rule

Do not start Phase 5 until the voice-note lifecycle, correction behavior, local-processing guarantees, and deletion behavior have been verified.
---

## PHASE 5 — CONSIDERATE, OPT-IN CHECK-INS

**Status:** Planned. Off by default.

### Objective

Allow the companion application to suggest occasional, thoughtful follow-ups based on relevant memories and user-configured preferences.

This feature must support companionship without creating pressure to engage.

### Scope

- Opt-in activation.
- User-controlled frequency.
- Configurable active hours and quiet periods.
- A complete disable option.
- Evidence-based check-in generation using retrieved memories.
- Clear attribution of why a check-in was suggested.
- Basic scheduling and eligibility logic.
- App-open-only delivery in the initial implementation.

### Honesty requirements

The companion must not claim that a check-in was spontaneous human volition.

When asked why it checked in, it should honestly explain the relevant context and application behavior.

For example, it may explain that a check-in was suggested because the user previously mentioned an upcoming interview.

It must not claim to have been worrying, waiting, missing the user, or independently thinking about them.

### Emotional safety

- No guilt for not replying.
- No manipulative streaks or notice-baiting.
- No fabricated distress or possessiveness.
- No invented events or deadlines.
- No excessive reminders.
- No check-in when the feature is disabled or outside permitted hours.

### Background execution decision

The initial version must not introduce a resident OS daemon or background service.

Any future proposal for closed-app push notifications or scheduled background execution must be reviewed separately, including privacy implications, resource usage, Windows integration, and failure behavior.

### Acceptance criteria

- Check-ins are disabled by default.
- Users can enable, configure, and disable them.
- Time-window and quiet-period rules pass automated tests.
- Check-ins reference genuine memory evidence.
- No invented personal details are introduced.
- No check-in is delivered outside the configured rules.
- App-open-only operation is verified.

### Completion rule

Do not introduce background services or notification infrastructure without separate approval.

---

## PHASE 6 — FICTION UNIVERSES AND SEPARATE CANON

**Status:** Planned.

### Objective

Support fictional worlds, role-play, stories, characters, and their continuity without confusing them with real personal memories.

### Scope

- Universe-scoped entities and facts.
- Character relationships and attributes.
- Story events and continuity records.
- Source tracking for fictional canon.
- Clear separation between real-world personal memories and fictional records.
- Universe-specific retrieval.
- User correction of fictional facts.
- Explicit control over which fictional universe is active.

### Isolation rules

- Fictional facts must not leak into real personal memory.
- Memories from one fictional universe must not silently appear in another.
- Real personal information must not be rewritten as fictional canon without clear intent.
- Switching universes must change the relevant retrieval scope.
- Shared concepts must remain explicitly scoped when ambiguity exists.

### Acceptance criteria

- Fictional information is stored with a universe identifier.
- Retrieval respects the active universe.
- Regression tests demonstrate no cross-universe leakage.
- Real personal memories remain separate from fictional records.
- Correcting canon updates future retrieval.
- Existing real-world memory behavior remains intact.

### Completion rule

Do not start Phase 7 until universe-scoped retrieval and isolation tests pass.

---

## PHASE 7 — SEMANTIC RETRIEVAL AND HYBRID SEARCH

**Status:** Planned. Embedding model selection and download require approval.

### Objective

Improve memory retrieval when the user's wording differs from the wording stored in a memory.

Keyword search remains the baseline and fallback.

### Scope

- Evaluate local embedding options.
- Use the existing Ollama ecosystem where suitable.
- Compare semantic retrieval with keyword retrieval.
- Implement hybrid ranking if testing demonstrates an improvement.
- Version the retrieval index and support rebuilding it from canonical memory records.
- Add relevance and latency benchmarks.
- Preserve source attribution.
- Keep retrieval bounded by relevance and context budget.

### Approval requirement

Before downloading an embedding model, present:

- Model identity and size.
- Disk, RAM, and VRAM requirements.
- Expected impact on the existing local LLM.
- Network requirements for the one-time download.
- How the feature can be disabled or removed.
- The benchmark used to justify adopting it.

Do not download or install a new model without explicit approval.

### Acceptance criteria

- A defined benchmark measures retrieval quality.
- Semantic or hybrid search demonstrates measurable benefit over the keyword baseline.
- Latency remains acceptable on the actual machine.
- Retrieval respects privacy and source-tracking rules.
- The index can be rebuilt from canonical records.
- Keyword fallback works when semantic retrieval is unavailable.
- No existing retrieval or memory-deletion guarantees regress.

### Completion rule

Adopt semantic retrieval only if measured results justify its complexity and resource cost.

---

## PHASE 8 — FINAL ACCEPTANCE, PRIVACY, AND RELIABILITY AUDIT

**Status:** Planned.

### Objective

Verify the complete application against the product requirements and ensure that all implemented features are dependable, understandable, and consistent with the original vision.

### A. Functional audit

Verify:

- Local chat and streaming.
- Conversation persistence.
- Persona persistence.
- Memory extraction and source attribution.
- Cross-session memory retrieval.
- Memory review, correction, and deletion.
- Conversation deletion and dependent-memory behavior.
- Export.
- Voice recording, playback, transcription, and correction if implemented.
- Check-in settings and quiet periods if implemented.
- Fiction-universe isolation if implemented.
- Keyword and semantic retrieval if implemented.

### B. AI behavior audit

Verify that the companion:

- Maintains a stable persona.
- Uses relevant memories naturally.
- Does not invent personal facts.
- Admits when it does not know or remember something.
- Distinguishes real-world information from fictional content.
- Does not claim unsupported experiences or human-like intentions.
- Attributes check-ins honestly.
- Does not manipulate users through guilt or possessiveness.

### C. Privacy audit

Verify:

- No unapproved external API calls or telemetry.
- No unapproved model downloads.
- No hidden background services.
- Local storage behavior is documented.
- Deletion behavior matches actual implementation.
- Backup retention and data-export limitations are documented.
- Audio, transcripts, indexes, caches, and derived memories are included in the relevant privacy analysis.

### D. Reliability and regression audit

- Run the complete backend test suite.
- Run frontend type checking and production build.
- Test application restart behavior.
- Test database migration and recovery assumptions.
- Test missing-model, unavailable-service, and corrupted-data behavior.
- Confirm no Phase 1 regression.
- Review logs for sensitive information.
- Document known limitations rather than marking unsupported behavior as complete.

### E. Final audit report

Create `FINAL_AUDIT.md` containing:

- Each acceptance criterion.
- PASS, FAIL, or NOT IMPLEMENTED status.
- The evidence or command output supporting the status.
- Outstanding defects and risks.
- Known limitations.
- Privacy findings.
- Recommended follow-up work.

### Final completion rule

The project is complete only when implemented requirements are verified and outstanding requirements are honestly marked as incomplete. A passing test suite alone does not prove the entire product vision has been fulfilled.

---

# 4. MANDATORY IMPLEMENTATION PROCESS

These rules apply to every phase and every coding session.

## Step 1 — Recover context

Before doing any work, read this file and the current project documents.

At minimum, inspect:

- `PROJECT_ROADMAP.md`
- `PRD.md`
- `ARCHITECTURE.md`
- `AI_BEHAVIOR.md`
- `BUILD_PLAN.md`
- `MEMORY_SPEC.md`, if it exists
- Current progress and phase reports, if present

Inspect the actual repository and current database state when relevant. Do not rely on an earlier conversation as proof of the current implementation.

## Step 2 — Determine actual status

Distinguish between:

- Planned.
- Authorized.
- In progress.
- Implemented.
- Tested.
- Verified complete.
- Blocked.
- Deferred.

Never mark a feature complete merely because code exists.

## Step 3 — Identify the authorized task

Work only on the current approved phase and step.

Do not skip ahead to future features, introduce new dependencies, download models, alter unrelated architecture, or expand scope without approval.

## Step 4 — Plan the specific change

Before editing, identify:

- Files to change.
- Database impact.
- APIs and interfaces affected.
- Risks and dependencies.
- Tests required.
- Acceptance criteria.

Keep changes small and testable.

## Step 5 — Implement incrementally

Make the smallest coherent implementation for the authorized step.

Preserve working functionality. Use existing patterns where appropriate. Do not create placeholder features and report them as implemented.

## Step 6 — Verify

Run relevant tests and build commands. Report actual results, failures, and exit codes.

If a test fails, investigate and fix the cause rather than hiding the failure or weakening the acceptance criteria without justification.

## Step 7 — Report and stop

After completing the authorized step, report:

- Files changed.
- Functionality implemented.
- Tests executed and their actual results.
- Known limitations.
- Remaining risks.
- Next proposed step.

Stop and wait for approval before starting the next step.

## Step 8 — Maintain project documentation

Update the relevant documentation when an approved implementation changes actual behavior, architecture, interfaces, requirements, or known limitations.

Do not document planned features as completed. Keep phase reports and progress records synchronized with verified implementation state.

---

# 5. NON-NEGOTIABLE TECHNICAL AND PRODUCT CONSTRAINTS

1. Keep the application local-first.
2. Preserve existing Phase 1 functionality.
3. Do not introduce cloud dependencies or telemetry.
4. Do not download models or add dependencies without approval when the change requires explicit approval.
5. Keep the companion's core behavior separate from persona configuration and retrieved memories.
6. Require traceable evidence for personal memories.
7. Never silently reuse deleted or unsupported memories.
8. Keep real-world memories separate from fictional canon.
9. Never add TTS as part of the human-recorded voice-note phase.
10. Do not add a background daemon as part of the initial check-in implementation.
11. Avoid unnecessary dashboards and feature bloat.
12. Do not skip tests or claim success without verification.
13. Do not execute an entire multi-phase roadmap in a single implementation step.
14. When instructions conflict, stop and report the conflict instead of guessing.
15. If the current repository contradicts this document, report the discrepancy and propose a resolution before making risky changes.

---

# 6. HOW TO HANDLE SESSION EXPIRATION

This project may be developed across multiple short-lived coding-agent sessions.

Therefore, never assume that conversation history will survive.

At the end of each approved implementation step, maintain a durable progress record that includes:

- Current phase and step.
- Last completed verified milestone.
- Files changed.
- Tests and actual outcomes.
- Current architecture or schema changes.
- Unresolved decisions.
- Known defects and limitations.
- Exact next step.
- Whether the next step has been authorized.

At the beginning of a new session, reconstruct context from the repository and progress records.

If the progress record conflicts with the actual code or test results, the actual repository state and reproducible evidence take precedence. Report the discrepancy before proceeding.

---

# 7. DEFINITION OF SUCCESS

The final product should feel like one coherent companion rather than a collection of disconnected AI features.

It should remember meaningful things reliably, retrieve them when relevant, maintain a recognizable personality, support personal voice notes, provide optional and honest check-ins, and handle fictional worlds without contaminating real-world memory.

It should remain private, understandable, responsive, and under the user's control.

The implementation must evolve incrementally. Each feature must be tested, each privacy guarantee must match the actual behavior, and each milestone must be supported by evidence.

**The roadmap defines the destination. The current approved step defines what may be implemented today.**