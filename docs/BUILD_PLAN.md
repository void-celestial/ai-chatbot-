# Build Plan

## Operating environment assumptions

Verified during Phase 0 (2026-10-20):
- Windows 11 (build 10.0.26300)
- Node 24.19.0 / npm 11.17.0
- Python 3.14.6, available only via the `py` launcher; the `python` and `python3` aliases are Microsoft Store stubs. Run backend commands as `py -m ...` (for example `py -m pip install -r requirements.txt`) or fix the PATH/alias in Settings > Apps > Advanced app settings.
- Ollama 0.40.6 is installed and responds at `http://localhost:11434`
- Exact installed model tag: `qwen3.5:9b` (9.7B params, Q4_K_M, 6.6 GB, context length 262144, capabilities: completion/vision/tools/thinking)
- No local embedding model is installed yet; Phase 7 must confirm one with the user before any download
- 16 GB system RAM and RTX 4050 GPU
- Development in VS Code
- No cloud API requirement

## Phase 0 — Inspect before changing anything
1. Inspect the current project folder and existing files. (Done: doc-only workspace with README.md, PROMPTS.md, and the five spec files, now moved to `docs/`. No code, no git history.)
2. Run `ollama list` and record the exact model tag. (Done: `qwen3.5:9b`.)
3. Check Python and Node versions. (Done; see assumptions above.)
4. Confirm Ollama responds locally. (Done: `/api/tags` returned the model.)
5. Do not overwrite or delete existing files without explaining why. (Docs were moved into `docs/` with contents unchanged.)
6. Write a short environment report. (See `PHASE0_ENVIRONMENT.md`.)

Exit criteria: environment and model tag confirmed.

## Phase 1 (roadmap) — Local chat foundation — COMPLETE
1. Create React + TypeScript + Vite frontend.
2. Create FastAPI backend.
3. Add health endpoint and Ollama connectivity test.
4. Implement basic chat request and streaming if feasible.
5. Persist conversations and messages in SQLite.
6. Add loading, stop/retry if supported, and clear error states.

Exit criteria: user can chat locally and messages survive restart.

## Phase numbering note
`docs/PROJECT_ROADMAP.md` is the authoritative phase sequence. This file is a
historical build checklist: the section headings below are labeled with the
matching roadmap phase so the old Phase 2/Phase 3 numbering is not mistaken
for the current roadmap. Items in later roadmap sections are planned and are
not implemented unless this file says otherwise. Phase 1 is complete; Phase 2
(Steps 1–7 plus the acceptance repairs) is implemented and verified, and the
Step 8 final acceptance audit closed Phase 2 on 2026-10-22. Phase 3 (Steps 1–6)
is implemented and verified as of 2026-10-22. Phase 4 is implemented and tested
as of 2026-10-22, but real transcription is not verified because no whisper.cpp
model is installed (model download requires approval); Phase 5 has not started.

## Phase 2 (roadmap) — Durable memory foundation + persona — CLOSED (Steps 1–8 complete)
1. Add schema and migration for memories/entities/relationships/events/story universes. — DONE (memories + memory_history; entities/relationships/events/universes still pending).
2. Add extraction service with source IDs and confidence/status. — DONE (extraction + validation; provenance derived from actual message records; confidence is policy-derived, never taken from the model).
3. Add deduplication and conflict handling. — DONE in conservative form (normalized-statement duplicate skips; potential conflicts are skipped, never overwritten; semantic conflict detection/merging is still open).
4. Add memory viewer/editor/delete. — DONE for the stored-memory lifecycle (Phase 2, Steps 6–7): backend list with filters/pagination, detail with validated provenance + capped history, confirm, reject, correct, and soft delete; plus a frontend `Memories` review screen (filters, pagination, provenance, history, confirm/reject/correct/delete, loading/empty/error/conflict states). Source message content/previews and a debug panel are still not implemented.
5. Add retrieval context builder and source-aware debug panel. — PARTIALLY DONE: local keyword retrieval (FTS5 + LIKE fallback), lifecycle/provenance filtering, and the bounded untrusted evidence block in the prompt are implemented; the memory management API is implemented (Phase 2, Step 6); the source-aware debug panel is NOT STARTED.

Exit criteria: a previously mentioned fact can be retrieved with supporting source evidence. MET, with the provenance and correction contracts covered by the acceptance-repair regression tests.

## Phase 3 (roadmap) — Cross-session continuity + companionship polish — COMPLETE

**Authoritative scope:** `docs/PROJECT_ROADMAP.md` Phase 3. Phase 2 is closed,
and its acceptance contracts (provenance validation, correction validation,
persona persistence, retrieval eligibility, prompt ordering, migrations and
backups) are regression-protected by the existing suites.

**Implementation plan (2026-10-22):**
1. Conversation deletion with explicit derived-memory handling. Backend
   `DELETE /api/conversations/{id}` deletes the conversation and its messages in
   one transaction after transitioning derived memories to `orphaned` (with a
   history reason). UI confirmation explains what is deleted, what is retained,
   and that backups may still contain copies. Tests: deletion, orphaning,
   retrieval exclusion, isolation of other conversations, history retention.
   — DONE (live check: conversation 204/404, derived accepted memory
   `orphaned` with history reason, export counts and post-deletion state
   correct).
2. Data export. Backend `GET /api/export` returns the current archive
   (conversations, messages), persona, all memories (including inactive ones),
   and memory history, with an explicit `scope` block naming what is excluded
   (FTS index, migration backups). A sidebar `Export data` action downloads the
   JSON file. Tests: scope/shape, deleted-memory inclusion, post-deletion
   output, no secrets. — DONE (live check: documented scope/counts, inactive
   memories included, no secrets).
3. Outdated memories. `POST /api/memories/{id}/supersede` transitions
   `accepted`/`confirmed` memories to `superseded` with a reason and history
   entry; retrieval already excludes that status. Review UI gains a
   confirmation action and copy explaining exclusion. Tests: allowed/rejected
   transitions, no mutation on 409, retrieval effect. — DONE (live check: 200
   with history reason, repeat request 409, retrieval excludes it).
4. Per-reply memory sources. Assistant messages store a bounded
   `memory_evidence` trace in the existing `metadata_json` column (memory id,
   statement, kind, origin, status, validated source ids), exposed by the
   messages API and shown in a disclosure under the reply. This makes the
   memory/archive relationship inspectable without fabricating previews.
   Tests: trace contents, prompt/evidence consistency, no trace when none was
   supplied, no metadata for user messages. No schema migration. — DONE (live
   check: assistant `metadata.memory_evidence` matched the retrieved memory;
   user messages had none).
5. Retrieval-driven continuity. When the current message produces no memory
   evidence (for example a pronoun-only follow-up), retrieval retries once using
   the previous user message in the same conversation as bounded context. The
   result is flagged `query_expanded` in logs/metadata-free diagnostics; it
   never crosses conversations, never bypasses lifecycle/provenance/budget
   filters, and never runs when the primary query already found evidence.
   Tests: pronoun follow-up recall, no expansion on a satisfied query, empty
   context, stale provenance still excluded. — DONE, including a runtime
   regression found and fixed: merging current-message terms with the earlier
   message's terms raised the coverage floor, so "Where does she live?" found
   nothing. The retry now uses the earlier message's terms alone.
6. Documentation and final verification. Update roadmap, architecture, memory
   spec, AI behavior, PRD, and README; run the full backend/frontend/build/lint
   suite and disposable-database runtime checks. — DONE (2026-10-22; see the
   progress record below).

**Decisions made within the roadmap (no approval needed):**
- Conversation deletion removes the conversation row and its messages (hard
  delete of the archive for that conversation). Derived memories are **not**
  deleted: they retain their row and history, become `orphaned`, and are
  excluded from retrieval. Backups may retain pre-deletion copies, so no
  complete-erasure claim is made; the UI recommends exporting first.
- Export scope is the local current state plus all stored memories/history. FTS
  index contents and migration backups are explicitly excluded because they are
  derived/backup copies, not canonical records.
- Continuity summaries on reopening a conversation are deferred: no evidence
  from this project shows they help, and generated summaries would add a new
  fabrication surface. Retrieval plus per-reply sources are used instead.
- Archive message search is considered and deferred: Phase 3 acceptance is met
  by memory retrieval and source surfacing; message search remains a documented
  optional/deferred item rather than an invented requirement.
- No migration or new dependency is required: `messages.metadata_json`, the
  `orphaned`/`superseded` statuses, and the existing schema cover all steps.

**Acceptance mapping:** cross-session recall (step 5 plus existing provenance
retrieval), source access (steps 3–4 and the existing memory review screen),
non-domination (step 5 gating plus existing thresholds/budgets), conversation
deletion effects (step 1), export scope (step 2), no chat/UI regressions
(step 6 full suite). All Phase 3 items were verified on 2026-10-22; the evidence
is in the progress record below.

Historical note: conversation sidebar/list and opening conversations were
already implemented before Phase 3. Archive message search remains unimplemented
(optional in the roadmap); user backup guidance is limited to migration
snapshots and the new export.

## Phase 4 (roadmap) — Human-recorded voice notes — IMPLEMENTED, MODEL INSTALL PENDING

**Compatibility spike (2026-10-22):**
- `ffmpeg 9.0.1` is installed (WinGet/Gyan build) and its `whisper` audio filter
  is present (`ffmpeg -h filter=whisper`), i.e. the build links whisper.cpp and
  exposes `model`, `language`, `use_gpu`, `format` (text|srt|json), and
  `destination` options. No separate `whisper.cpp` binary or `faster-whisper`
  package is installed, and no GGML model exists in the usual cache locations.
  Only `qwen3.5:9b` is installed in Ollama, which cannot transcribe audio.
- `faster-whisper 1.2.1` / `ctranslate2 4.8.3` are published, but using them
  would add a large Python/CT2 dependency stack on Python 3.14 and still need a
  downloaded model. The already-installed ffmpeg whisper filter is the simplest
  local path and adds no dependency; it also supports CPU/GPU selection so it
  can avoid competing with Ollama for VRAM (default: CPU).
- Spike command that confirmed the filter works syntactically (fails only on
  the intentionally missing model): `ffmpeg -i tone.wav -af
  "whisper=model=model.bin:language=auto:use_gpu=0:format=json:destination=out.json"
  -f null -` with `cwd` set to the model/output directory. Absolute Windows
  paths in the filter are awkward to escape, so the integration runs ffmpeg
  with a relative model/output path; the default model path is project-local
  (`data/models/ggml-base.en.bin`).
- **Blocker:** no transcription model may be downloaded without explicit user
  approval. Integration, storage, API, UI, and tests with a fake engine are
  implemented first; real transcription remains unverified until a model is
  approved and placed at `WHISPER_MODEL_PATH`.

**Plan:**
1. Migration `0003_voice_notes.sql`: `voice_notes` metadata/transcript table, no
   audio bytes in SQLite.
2. Storage service: generated `<id>.<ext>` filenames under
   `data/voice_notes/`, streamed size limit, extension allowlist, path
   containment checks, best-effort duration probe, deletion semantics.
3. Transcription service: small engine interface, ffmpeg/whisper.cpp
   implementation with configurable binary/model/language/GPU/timeout, typed
   failures, in-process background task with duplicate prevention and startup
   recovery of interrupted jobs. Fake engine for tests.
4. API: list/detail, raw-body upload (no new multipart dependency), audio
   streaming, transcribe/retry, transcript correction, audio deletion, note
   deletion.
5. Frontend: `Voice notes` sidebar view with MediaRecorder recording (feature
   detected), local file import, playback, status/retry, transcript editing,
   delete-audio/delete-note confirmations, light/dark and keyboard support.
6. Tests and documentation, then full verification with disposable storage.

Acceptance mapping: recording/playback/persistence, local transcription,
correction use, explicit deletion semantics, no TTS/cloud path, and full-suite
regression are covered by steps 2–8. The model download remains a separate
approval item, so Phase 4 cannot be marked fully accepted until a real model
is installed and end-to-end transcription is verified.

**Implementation result (2026-10-22):** steps 1–6 are complete. The recording,
import, playback, transcript, correction, explicit transcript-to-memory, and
deletion flows work with a fake engine in tests and were exercised through the
real FastAPI app on a disposable database. The ffmpeg whisper filter is detected
and configured, but `POST .../transcribe` returns 503 `model_missing` because the
model is absent. No model or engine was downloaded. Phase 4 therefore remains
unaccepted on "transcription runs locally using the approved engine" and
"no external network call" (the latter is satisfied by code review, not observed
with a real engine). Installing a whisper.cpp GGML model at `WHISPER_MODEL_PATH`
is the only remaining setup step; it requires user approval.

## Phase 5 (roadmap) — Opt-in check-ins (planned)
Planned; off by default and not implemented.

## Phase 6 (roadmap) — Story continuity (planned)
1. Create and switch between story universes.
2. Add character/entity editor and aliases.
3. Track relationships and timeline events.
4. Mark ideas tentative vs canon.
5. Test same-name characters across separate universes.

Exit criteria: no cross-universe leakage in regression tests.

## Phase 7 (roadmap) — Semantic retrieval (planned)
1. Confirm an available local embedding model with Ollama.
2. Implement local embedding generation and persistence.
3. Combine lexical and semantic results.
4. Add fallback to lexical search.
5. Benchmark recall and latency against a small local test set.

Exit criteria: semantic search improves retrieval without breaking privacy or source tracing.

## Phase 8 (roadmap) — Final acceptance, privacy, and reliability audit (planned)
1. Functional, behavior, privacy, and reliability audit.
2. Produce `FINAL_AUDIT.md` with PASS/FAIL/NOT IMPLEMENTED evidence.

Final polish/hardening backlog:
- Responsive layout and light/dark themes
- Accessibility and reduced motion
- Error boundaries and useful diagnostics
- Automated tests and lint/type-check
- Security review for localhost binding, CORS, input validation, and no arbitrary shell execution
- README with exact setup/start commands and troubleshooting

## Required workflow for coding agent
At each phase:
1. Inspect current code and docs.
2. State a brief plan.
3. Implement only that phase.
4. Run tests/build/type-check.
5. Fix failures rather than merely listing them.
6. Report files changed, commands run, actual results, known limitations, and the next phase.
7. Do not claim a check passed unless it was run and the exit status/result was observed.
8. Never replace the existing architecture wholesale without a clear reason and user approval.

## Progress record
Counts as reported milestones, not claims beyond documented evidence.

### 2026-10-21 — Phase 2, Step 4 (roadmap): memory extraction and validation COMPLETE
- Files added: `backend/app/services/memory_extraction.py`, `backend/app/tests/test_memory_extraction.py`.
- Files changed: `backend/app/api/chat.py` (background-task scheduling after SSE responses), `backend/app/services/ollama_client.py` (single-shot JSON call), `backend/app/config.py` (extraction timeout/candidate limit), `backend/app/tests/conftest.py` (extraction fake).
- Pipeline: newest exchange (last user message plus the assistant replies after it) → local Ollama via the existing client → JSON candidates → application-side validation → persistence through the existing memory service. Runs as an in-process FastAPI background task after the chat response; its own DB connection; extraction failures cannot break chat streaming or message persistence.
- Provenance: derived from actual stored message records (verbatim quote anchoring + index hint); source IDs are never taken from the model. `user_stated` only when the validating quote sits in a genuinely user-authored message; assistant-supported claims are downgraded to `inferred`/`review`.
- Reported backend suite at this checkpoint: 131 passed, 0 failed.
- Known limitations documented: verbatim quote anchoring skips unanchored paraphrases; contradiction detection is a heuristic (negation insertion); sensitive-data filtering is a conservative denylist that may over-block (skipping, never storing); same-subject potential conflicts are skipped rather than resolved (no semantic conflict detection yet); only the newest exchange is processed (no backlog extraction); an in-process extraction task can be lost if the application exits (chat output and message persistence are unaffected); no automatic retries.
- NOT implemented in this step: memory retrieval/FTS search, prompt injection of memories, memory API endpoints, review UI, conflict merging/conflict marking.

### 2026-10-21 — Phase 2, Step 5 (roadmap): memory retrieval and prompt integration COMPLETE
- Files added: `backend/app/services/memory_retrieval.py`, `backend/app/tests/test_memory_retrieval.py`.
- Files changed: `backend/app/services/prompt_builder.py` (delimited memory evidence block + budget), `backend/app/services/chat_service.py` (retrieval call with failure isolation and metadata-only logging), `backend/app/config.py` (retrieval limits and evidence budget).
- Retrieval: current user message → meaningful terms (stopwords/short tokens removed, capped at 12) → candidates from FTS5 `memories_fts` prefix matches over statement/keywords plus a subject `LIKE` scan when available (the index does not cover `subject`); otherwise a parameterized LIKE scan, with both paths expanding the same plural variants as the scorer → deterministic scoring (keyword 3, subject 3, statement 2 per matched term) → threshold and coverage floor → sorted by score, coverage, recency, id → duplicates collapsed → provenance re-validated against `conversations`/`messages` → limited result pack. Only `accepted`/`confirmed` memories are eligible; `review`, `conflicted`, `superseded`, `orphaned`, and `deleted` are never returned. An empty query returns nothing rather than recency-only fallback.
- Prompt: core behavior rules first, then persona settings plus guardrail, then a bounded memory evidence block explicitly framed as untrusted reference data whose embedded instructions must be ignored. Each record shows kind, origin (`stated by the user` vs `inferred`), confidence, status, and source message ids; statements are whitespace-flattened and truncated. The block has its own character budget (`MEMORY_PROMPT_MAX_CHARS`), independent of the recent-history budget, so memories cannot displace all history. Empty retrieval adds an explicit "no stored memories included" note instead of inventing recall.
- Failure isolation: FTS5 absence falls back to LIKE; database or formatting errors yield no memory evidence and never interrupt streaming or message persistence. Logs carry counts, reason codes, and memory ids only.
- Reported backend suite: 179 passed, 0 failed (`py -m pytest backend/app/tests`; 131 before + 48 new). Targeted run: `py -m pytest backend/app/tests/test_memory_retrieval.py` — 48 passed.
- NOT implemented in this step: memory API endpoints, review UI, embeddings/semantic search, archive message search, entity/story-universe scope, source-aware debug panel, conflict merging.

### 2026-10-21 — Phase 2, Step 6 (roadmap): memory management API COMPLETE
- Files added: `backend/app/api/memories.py`, `backend/app/tests/test_memory_api.py`.
- Files changed: `backend/app/services/memory_service.py` (bounded listing, batched provenance validation, capped history window, confirm/reject/delete policy helpers), `backend/app/models/schemas.py` (list/detail/provenance/review models; corrections reject unknown fields), `backend/app/config.py` (pagination/history limits), `backend/app/main.py` (router + DELETE in CORS methods).
- API: `GET /api/memories` (status/kind/origin filters, limit 1–100 default 50, offset, `total`), `GET /api/memories/{id}` (memory + validated provenance + newest 100 history entries), `POST /api/memories/{id}/confirm`, `POST /api/memories/{id}/reject`, `PATCH /api/memories/{id}`, `DELETE /api/memories/{id}` (204). IDs are validated as 32-character lowercase hex; malformed ids return 422, unknown ids 404.
- Lifecycle: confirm allows `review`/`accepted` → `confirmed` (origin, confidence, and provenance untouched); reject allows only `review` → soft `deleted` with the reason recorded in `memory_history`; corrections (statement/subject/keywords only) are allowed for `accepted`/`review`/`confirmed` and keep the previous snapshot; delete is an idempotent soft delete. Ineligible or repeated transitions return 409. No client-supplied status/origin/confidence/source fields are accepted (422 on unknown fields).
- Deletion/retrieval: deleted and rejected records are excluded by the existing status filters in both FTS5 and LIKE retrieval; source conversations/messages are never deleted; no rows or history are hard-deleted, so permanent erasure is not claimed.
- Errors: service failures map to a 500 with a fixed safe message (no exception text, paths, or stack traces); simultaneous memory + history writes roll back together.
- Reported backend suite: 228 passed, 0 failed (`py -m pytest backend/app/tests`; 179 before + 49 new). Targeted run: `py -m pytest backend/app/tests/test_memory_api.py` — 49 passed.
- NOT implemented in this step: memory review UI or any frontend change, archive search, entity/story scope, embeddings/semantic search, conflict merging, source-aware debug panel, hard deletion/erase endpoint.

### 2026-10-21 — Phase 2, Step 7 (roadmap): memory review UI COMPLETE
- Files added: `frontend/src/components/MemoryView.tsx`, `MemoryList.tsx`, `MemoryDetail.tsx`, `MemoryStatusBadge.tsx`, `frontend/src/hooks/useMemories.ts`, `frontend/src/memoryUtils.ts`, `frontend/src/api/client.test.ts`, `frontend/src/components/MemoryView.test.tsx`, `frontend/src/App.test.tsx`, `frontend/src/test/setup.ts`, `frontend/src/test/harness.tsx`, `frontend/vitest.config.ts`.
- Files changed: `frontend/src/api/client.ts` (typed memory list/detail/confirm/reject/correct/delete calls), `frontend/src/types.ts` (memory types aligned with the backend schemas), `frontend/src/components/Sidebar.tsx` (`Memories` entry with `aria-current`), `frontend/src/App.tsx` (`chat`/`memories` view state, source-conversation navigation), `frontend/src/styles.css` (memory layout, states, responsive and theme styling), `frontend/package.json` + lockfile (dev-only `vitest` and `jsdom`, `test` script), `README.md`, docs.
- UI: sidebar entry opens a full-pane memory screen. List shows statement, kind, origin, lifecycle badge, provenance warning, and updated time; status/kind/origin filters match the API enums, including a one-click `Needs review` filter; pagination uses 20 per page and the API's `total`, with Previous/Next disabled at the boundaries. Detail shows statement, status/origin/kind, confidence, created/updated/last-confirmed timestamps, subject, keywords, validated provenance (conversation id or an `Open source conversation` button only when that conversation is loaded in the sidebar, source message ids, invalid-source warning), and capped history with status changes, reasons, and before/after statements.
- Actions: `Confirm memory` only for `review`/`accepted`; `Reject…` only for `review`, with an optional reason and an explanation of retention; `Edit…` only for `accepted`/`review`/`confirmed`, editing only statement/subject/keywords with inline validation; `Delete…` behind an in-app confirmation that states the soft-delete behavior and that source conversations/messages are untouched. Pending actions disable controls and show progress; detail + list + counts refetch after success; 409/422/404/network errors surface without a false success state.
- Honesty: rejected and manually deleted records share the backend `deleted` status, so both are labelled `Inactive`; the UI only distinguishes them when history records a rejection reason. No permanent-erasure claim is made.
- Tests: frontend production build (`npm run build`) and lint (`npm run lint`, 0 errors) pass; `npm test` — 35 tests in 3 files pass. Browser verification was run against a temporary database with headless Chrome: list/pagination/filter, detail/provenance, source-conversation navigation, confirm, edit validation + save, 409 conflict + resync, reject with reason, delete confirmation + soft-delete copy, blocked-network error + retry, empty-vs-error states, theme toggle, keyboard focus/Escape on confirmation panels, and console hygiene.
- Backend regression: `py -m pytest backend/app/tests --tb=short` — 228 passed, 0 failed (unchanged from Step 6).
- Known limitations: source conversation navigation is available only when the conversation is in the loaded sidebar list (otherwise the id is displayed); source message content/previews and a source-aware debug panel remain unbuilt; no permanent erase/export; no archive message search, entity/story scope, embeddings, or conflict merging; UI is a single view without deep links.

### 2026-10-21 — Phase 2 acceptance repair: provenance and correction validation COMPLETE
- Files changed: `backend/app/services/memory_service.py` (single `resolve_source_references` policy used by the API and retrieval), `backend/app/services/memory_retrieval.py` (validated ids only reach prompt evidence; deleted/mismatched messages skip the record), `backend/app/models/schemas.py` (explicit null statement/keywords rejected; provenance contract documented), `backend/app/api/memories.py` (provenance/validation documentation), `backend/app/tests/test_memory_api.py`, `backend/app/tests/test_memory_retrieval.py`, `frontend/src/components/MemoryView.test.tsx`, and the docs (`README.md`, `docs/PROJECT_ROADMAP.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/AI_BEHAVIOR.md`, `docs/MEMORY_SPEC.md`, this file).
- Message provenance: every declared source message must still exist and belong to the declared source conversation. A surviving conversation cannot rescue a deleted message reference; the memory is excluded from retrieval and prompt formatting, and stale ids are never printed as verified sources. Legacy conversation-only records remain valid while their conversation exists; message-only records remain valid without being tied to the current conversation. No migration and no id rewriting.
- Correction validation: `PATCH /api/memories/{id}` with `{"statement": null}` (and `{"keywords": null}`) returns 422 before any write; the record and history are unchanged. Omitted fields keep their values, valid partial corrections still work, and corrections still change future lexical retrieval.
- Tests: `py -m pytest backend/app/tests --tb=short` — 239 passed, 0 failed. `npm test` — 36 passed across 3 files. `npm run build` — passed. `npm run lint` — 0 errors, 4 advisory warnings.
- Not done at that checkpoint: no migration, no embeddings/archive search, no browser E2E rerun for this repair; Step 8 and Phase 3 followed in later sessions.

### 2026-10-22 — Phase 3, Steps 1–6 (roadmap): continuity and companionship polish COMPLETE
- Files added: `backend/app/api/export.py`, `backend/app/tests/test_conversation_deletion.py`, `backend/app/tests/test_export.py`, `backend/app/tests/test_memory_trace.py`, `frontend/src/components/MessageList.test.tsx`.
- Files changed: `backend/app/api/conversations.py` (DELETE endpoint), `backend/app/api/chat.py` (trace scheduling unchanged), `backend/app/api/memories.py` (supersede), `backend/app/services/chat_service.py` (transactional deletion + orphaning, per-reply evidence metadata, follow-up context), `backend/app/services/memory_service.py` (supersede policy, orphan transition), `backend/app/services/memory_retrieval.py` (bounded follow-up retry), `backend/app/models/schemas.py` (metadata/message payloads), `backend/app/config.py` (extraction timeout default), `backend/app/main.py` (export router), frontend `App.tsx`, `Sidebar.tsx`, `MessageList.tsx`, `MemoryDetail.tsx`, `useMemories.ts`, `memoryUtils.ts`, `api/client.ts`, `types.ts`, `styles.css`, `App.test.tsx`, `MemoryView.test.tsx`, `api/client.test.ts`, plus `.env.example`, `README.md`, and the docs.
- Conversation deletion: `DELETE /api/conversations/{id}` deletes the conversation row and its messages in one transaction after moving derived memories to `orphaned` with a history reason. Memories already `deleted`/`superseded`/`conflicted` are left as-is. Backups and exports are untouched, so no complete-erasure claim is made; the UI recommends exporting first.
- Export: `GET /api/export` returns one JSON document with persona, conversations, messages, all memories (including inactive/orphaned), and memory history, plus an explicit `scope` block naming excluded derived/backup copies. The sidebar `Export data` action downloads it; failures are surfaced without a false success message.
- Outdated memories: `POST /api/memories/{id}/supersede` allows `accepted`/`confirmed` → `superseded` with a reason and history entry; other transitions return 409. The review UI exposes the action behind a confirmation and explains retrieval exclusion.
- Per-reply sources: assistant messages store a bounded `memory_evidence` trace (memory id, statement, kind, origin, status, validated source ids) in the existing `messages.metadata_json`; `MessageList` shows it in a `Memory used (n)` disclosure. User messages carry no trace, and no trace is written when retrieval supplied nothing.
- Follow-up retrieval: when the current message produces no evidence, retrieval retries once with the previous user message's terms. A runtime check exposed a coverage-floor defect in the merged-term implementation ("Where does she live?" after "Where did Mara move to again?" found nothing); the retry now uses the context terms alone, still within the same lifecycle/provenance/rank/budget pipeline. The regression is covered by `test_pronoun_followup_with_a_non_matching_current_term_still_recalls`.
- Verification (independently re-run on this checkout): backend suite 275 passed, 0 failed (`py -m pytest backend/app/tests`); frontend `npm test` 51 passed across 4 files; `npm run build` passed; `npm run lint` 0 errors and 4 advisory React Compiler warnings.
- Disposable-database runtime checks through the real FastAPI app: chat with `qwen3.5:9b`, extraction + persistence after restart, cross-conversation retrieval, `memory_evidence` on the assistant message only, supersede (200, history reason, repeat 409), conversation deletion (204, then 404; derived memory `orphaned` with reason; unrelated conversation intact), export counts/scope before and after deletion, and the follow-up retry against the live database.
- Live extraction timing: 165–400s per exchange on this machine, above the previous 90s default, so `DEFAULT_EXTRACTION_TIMEOUT_SECONDS` is now 600 and `MEMORY_EXTRACTION_TIMEOUT` is documented in `.env.example`. No migration, schema change, or dependency was added.
- Known limitations: extraction is slow enough that a memory may appear minutes after the exchange and competes with the next chat generation on the single local Ollama instance; deletion/export are local-current-state operations, not erasure of backups or previous downloads; archive message search and continuity summaries are deferred; no permanent-erase endpoint.

### 2026-10-22 — Phase 4: human-recorded voice notes IMPLEMENTED (transcription acceptance blocked on model approval)
- Compatibility spike: installed ffmpeg 9.0.1 includes the `whisper` filter (whisper.cpp); no whisper binary, Python whisper package, or GGML model is installed, and Ollama has only the text model. Selected the ffmpeg filter to avoid new dependencies; CPU by default.
- Files added: `backend/app/db/migrations/0003_voice_notes.sql`, `backend/app/services/voice_notes.py`, `backend/app/services/transcription.py`, `backend/app/api/voice_notes.py`, `backend/app/tests/test_voice_notes.py`, `backend/app/tests/test_transcription.py`, `frontend/src/hooks/useVoiceNotes.ts`, `frontend/src/components/VoiceNotesView.tsx`, `VoiceNoteDetail.tsx`, `VoiceRecorder.tsx`, `frontend/src/components/VoiceNotesView.test.tsx`.
- Files changed: `backend/app/config.py`, `.env.example`, `backend/app/models/schemas.py`, `backend/app/main.py` (router, engine injection, restart recovery), `backend/app/tests/conftest.py` (fake engine/settings), `backend/app/tests/test_migration.py` (multiple migrations expectation), frontend `App.tsx`, `Sidebar.tsx`, `api/client.ts`, `types.ts`, `styles.css`, `App.test.tsx`, `api/client.test.ts`, `README.md`, and docs.
- Storage/API: generated `<id>.<ext>` files under `VOICE_NOTES_DIR`, 25 MB streamed cap, allowlisted content types, path containment, best-effort ffprobe validation/duration, atomic partial-file handling. Table `voice_notes` stores metadata, current transcript, last machine transcript, source/status/error, and audio-deletion timestamp. Endpoints cover list/detail/upload/playback/transcribe/correct/delete-audio/delete-note and the explicit "send transcript to chat" memory hook.
- Transcription: `FfmpegWhisperEngine` invokes `ffmpeg -af whisper=model=...:language=...:use_gpu=...:format=json:destination=...` with relative paths, a timeout, and typed failures (`ffmpeg_missing`, `model_missing`, `timeout`, `engine_failed`, `empty_transcript`, `invalid_output`, `model_path_not_relocatable`). Background task claims the row atomically, refuses to overwrite a user correction, publishes only while still `processing`, and startup marks interrupted jobs failed. Fake engine used by tests (no model/network).
- Frontend: sidebar `Voice notes` view with MediaRecorder feature detection, permission/unsupported errors, local file import, playback, list/detail, status/retry, transcript editing with machine-text disclosure, confirmations for replacing a correction/deleting audio/deleting a note, and an explicit transcript-to-memory action. Light/dark styles and keyboard/aria behavior follow the existing patterns.
- Verification: backend suite 318 passed, 0 failed; frontend `npm test` 70 passed across 5 files; `npm run build` passed; `npm run lint` 0 errors and 5 advisory warnings. Disposable-database runtime checks through the real FastAPI app confirmed upload 201, byte-identical playback, 1.0s duration probe, transcript correction persisted across restart, audio deletion (playback 410, transcript kept), note deletion (404, file removed), and transcript-to-conversation extraction. `POST .../transcribe` correctly returned 503 with `model_missing` because no model exists.
- Live transcription, model quality/latency, and observed no-network behavior are NOT verified. No model was downloaded and no dependency was added. Next: with user approval, install a whisper.cpp GGML model (e.g. `data/models/ggml-base.en.bin`) and rerun real transcription/playback/correction checks; only then can Phase 4 be marked accepted. Phase 5 has not started.
- Next after that step: Phase 4 (voice notes), which was authorized in a later session.
