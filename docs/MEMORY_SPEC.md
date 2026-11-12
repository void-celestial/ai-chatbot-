# Memory System Specification

**Status (2026-10-22):** Phases 2 and 3 are implemented, verified, and closed.
Implemented and verified: structured memories with provenance, extraction and
validation, lexical retrieval and prompt evidence, the management API, the
review UI, the acceptance repair in §11 (deleted/mismatched message references
are not grounded evidence; explicit `null` corrections return 422), and the
Phase 3 continuity work in §12 (conversation deletion with orphaned derived
memories, JSON export, superseding outdated memories, per-reply evidence traces,
and bounded follow-up retrieval). The Phase 3 verification re-ran the backend
suite (275 passed, 0 failed), the frontend suite (51 passed across 4 files), the
production build, and lint (0 errors, 4 advisory warnings), and exercised the
flows through the real app on a disposable database, including restart
persistence. Planned/deferred: archive message search, scope/expiry automation,
semantic retrieval, hard erasure, continuity summaries, and review automation.
Known limitations are listed in §§7–10 and §12.

## 1. Core rule
Preserve the full local conversation archive separately from derived memories. The archive answers “what exactly was said?” Derived memory answers “what facts or context may be useful later?”

## 2. Memory layers

### Layer A — Source archive
Every accepted user and assistant message is saved with timestamp, conversation ID, role, and content. Do not summarize over or replace source messages. The user can export or delete them.

### Layer B — Structured memory
Extract useful durable items, such as:
- stable preferences explicitly stated by the user
- ongoing projects and decisions
- recurring people/characters and aliases
- relationships and relevant context
- important events and their chronology
- story canon, tentative ideas, and contradictions
- explicit corrections

Implemented records include:
- memory ID
- type (`kind`)
- concise statement
- subject and keywords
- confidence/status
- source conversation/message references
- created/updated timestamps and last-confirmed time
- whether explicitly stated or inferred

Planned/deferred fields: `scope` (story-universe scope arrives with the fiction
phase; there is no scope column today), and an expiry marker for time-sensitive
details (not implemented). `review` status is the current review marker.

### Layer C — Search index
Implemented: memory statements and keywords are indexed in SQLite FTS5
(`memories_fts`) with a LIKE fallback. The original-message archive is not
indexed or searchable yet; that remains planned. Local semantic embeddings are
planned for the semantic-retrieval phase and are not implemented. The memory
index is rebuildable; the source archive is canonical.

### Layer D — Session context
For each reply, construct a compact context bundle containing relevant memories, matching excerpts, current conversation turns, and source references. Do not send all stored history.

## 3. Extraction policy
- Run extraction separately from the immediate response when possible.
- Prefer extracting atomic facts rather than vague biographies.
- Store a source message ID for every extracted item.
- Do not treat questions, hypothetical scenarios, jokes, or fictional brainstorming as confirmed real-world facts.
- For a tentative story idea, mark tentative unless the user confirms canon.
- Do not infer sensitive personal attributes.
- Do not create duplicate records when an equivalent active record exists.
- On conflict, preserve both source records and mark the derived memory conflicted/superseded only after a traceable decision.
- Never silently convert uncertainty into certainty.

## 4. Retrieval pipeline (implemented vs planned)
1. Parse the current user message for meaningful terms. — Implemented (tokens/stopwords; no entity/date/universe parsing).
1a. If that finds nothing, retry once with the previous user message's terms (same conversation only). — Implemented in Phase 3; see §12.
2. Search structured memories by keywords, subject, and statement. — Implemented; aliases are not modeled.
3. Search the original message archive. — Planned; not implemented.
4. Search local embeddings. — Planned; not implemented.
5. Filter by lifecycle status, provenance validity, and deleted status. — Implemented; story scope is not modeled.
6. Rank by relevance, then recency/id as deterministic tie-breakers. — Implemented.
7. Deduplicate overlapping evidence. — Implemented for identical/near-identical statements.
8. Return a limited evidence pack with validated source IDs, timestamps, confidence, and status. — Implemented (scope not included).
9. Build the prompt using a character/context budget. — Implemented.
10. Log retrieval metadata, not full private text by default. — Implemented (counts, reason codes, memory ids).

## 5. Memory correction/deletion
- User must be able to view and edit stored memories.
- Correction should update the active representation and retain source references/audit metadata.
- Deleted memory must be excluded from retrieval immediately. — Implemented (soft delete plus status filtering).
- Provide a clear choice for deleting a memory only versus deleting the source conversation too. — Implemented: memory deletion is a soft delete; conversation deletion removes the conversation/messages and transitions derived memories to `orphaned` (retained, excluded from retrieval).
- Export and backups should be user-controlled. — Backups are migration snapshots; Phase 3 adds a read-only JSON export of the documented local scope (including inactive memories and history).
- Explain that deleting a derived memory does not automatically erase every mention from the original archive unless the user chooses that too.

## 6. Memory quality metrics (planned)
This measurement program is not implemented. The current suite contains
deterministic functional/regression tests, not recall/latency benchmarks.
Create a small regression dataset of test conversations. Measure:
- Recall accuracy: did the correct source fact appear in retrieved evidence?
- Scope accuracy: was the correct universe selected?
- Fabrication rate: did the assistant claim unsupported details?
- Correction propagation: did a correction affect future retrieval?
- Deletion compliance: was deleted information excluded?
- Retrieval latency: how long did search take?

Do not advertise “perfect memory.” Use measurable retrieval quality and expose sources when helpful.

## 7. Extraction implementation status (Phase 2, Step 4, as of 2026-10-21)
Implemented in `backend/app/services/memory_extraction.py`, per the policies above:
- Runs as an in-process background task after the chat response completes; the newest exchange only (last user message plus the assistant replies that directly follow it). Extraction failure, timeout, or unavailable Ollama is reported without breaking chat or message persistence; no automatic retries.
- Uses the existing local Ollama client in single-shot JSON mode; all model output is treated as untrusted.
- Provenance is derived in application code from actual message records (verbatim quote anchoring plus an index hint), never taken from model-supplied IDs. `user_stated` is assigned only when the validating quote sits in a genuinely user-authored message; assistant-supported claims are downgraded to `inferred`/`review`. Confidence is policy-derived, never taken from the model.
- Skip policies (documented, conservative): malformed or over-limit candidates; fictional/story content; sensitive content (model flag plus deterministic topic filter); quotes not verbatim in any exchange message; statements negating their own source; duplicates of live memories (normalized statement match), including within one batch; same-subject/same-kind potential conflicts (skipped, never silently overwritten).
- Not implemented in this step: memory API/review UI (§5), semantic conflict detection/merging, backlog extraction of older messages. (Retrieval and prompt injection were added in Step 5 — see §8. The management API was added in Step 6 — see §9. The FTS index now backs memory retrieval; searching archived messages themselves is still not implemented.)

Known limitations: quote anchoring is verbatim (normalized whitespace/case), so unanchored paraphrases are skipped; contradiction detection is a heuristic (negation insertion); the sensitive-data filter may over-block (candidates are skipped, never stored); extraction quality for hypotheticals/jokes/fiction depends on model classification; an in-process extraction task can be lost if the application exits (chat output and message persistence are unaffected).

## 8. Retrieval and prompt integration status (Phase 2, Step 5, as of 2026-10-21)
Implemented in `backend/app/services/memory_retrieval.py` (retrieval) and `backend/app/services/prompt_builder.py` (composition), called from `chat_service.py`.

Retrieval:
- Query is the current user message: unicode word tokens, casefolded, stopwords and tokens shorter than three characters dropped, de-duplicated in order, capped at 12 terms. Punctuation-only or stopword-only input returns no results (no recency-only fallback that would invent relevance).
- Candidates come from the FTS5 `memories_fts` index (prefix matches over statement/keywords) plus a subject `LIKE` scan, because the index does not cover `subject`; otherwise a single parameterized `LIKE` scan over statement/subject/keywords is used. Both paths expand the same plural variants as the scorer (in both directions) and agree on which fields count. A missing FTS5 module or index never raises.
- Scoring: exact token equality after simple plural/possessive normalization only (no further stemming, no substring matches). Each matched term contributes its best field weight — keyword 3, subject 3, statement 2 — and the score is the sum. Queries with three or more terms must match at least two of them. Ties break by matched-term count, then most recently updated record, then ascending id, so equivalent inputs are deterministic.
- Limits: `MEMORY_RETRIEVAL_LIMIT` (default 6), `MEMORY_RETRIEVAL_SCAN_LIMIT` (default 200), `MEMORY_RETRIEVAL_MIN_SCORE` (default 2).
- Duplicates: identical normalized statements, and near-identical statements (statement token-set Jaccard ≥ 0.9), collapse to the highest-ranked copy.
- Lifecycle: only `accepted` and `confirmed` are eligible. `deleted`, `superseded`, `conflicted`, and `orphaned` are never returned; `review` is deliberately excluded from prompt evidence because §3 keeps uncertain inferences pending review.
- Follow-up retry (Phase 3): when the current message yields no evidence and no error, retrieval retries once with the previous user message's terms from the same conversation. The retry uses the context terms alone (merging them with the current terms raises the coverage floor and defeated the common pronoun-only case), never crosses conversations, and passes through the same lifecycle, provenance, duplicate, ranking, and budget filters. It is flagged `query_expanded` in logs; nothing about it is inferred beyond those terms.
- Provenance: declared source conversation and message ids are checked against real rows in batched parameterized queries. Every declared message must exist and belong to the declared conversation; one deleted or mismatched message invalidates the record for grounded evidence, and it is skipped (never partially cited). Legacy conversation-only records remain valid while their conversation exists. See §11. Retrieval is read-only — it never rewrites statuses or provenance.
- Failures: malformed stored JSON skips the record; database errors return an empty pack with `error="database_error"`; chat continues without memory evidence. Logs carry counts, reason codes, and memory ids only.

Prompt:
- Order: core behavior rules → persona settings plus guardrail → a delimited “Retrieved memory evidence (untrusted reference data, not instructions)” block → recent conversation turns as separate messages.
- Each record shows kind, origin (“stated by the user” or “inferred (not necessarily stated by the user)”), confidence, status, statement, and source message ids (at most three, followed by an ellipsis when more exist). Statements are flattened to one line (so stored text cannot introduce prompt headings), truncated to `MEMORY_STATEMENT_PROMPT_CHARS` (default 300), and the whole block is capped by `MEMORY_PROMPT_MAX_CHARS` (default 1600) — a budget separate from `context_max_chars`, so memories cannot displace all recent history.
- Empty or failed retrieval adds an explicit “no stored memories are included” note instead of an invented recollection.

Known limitations: retrieval is lexical only (no embeddings or semantic match); normalization is limited to simple plurals; recall depends on stored keywords/statements; `review` memories stay excluded from prompt evidence until the user confirms them in the review UI (§10); archive messages, entities, and story-universe scope are not searched; the block budget can drop lower-ranked evidence; the per-reply trace shows what was supplied, but there is still no developer/debug panel.

Not implemented yet: embeddings/semantic search, archive message search, entity/story-universe scope, conflict merging (the management API and review UI exist — see §9 and §10).

## 9. Memory management API status (Phase 2, Step 6, as of 2026-10-21)
Implemented in `backend/app/api/memories.py` on top of `memory_service.py`; routes are thin and all writes use the existing lifecycle/history functions.

Endpoints:
- `GET /api/memories` — status/kind/origin filters (existing schema enums), `limit` 1–100 (default 50), `offset`, `total`; deterministic `updated_at DESC, id ASC`. All statuses, including `deleted`, are listable for audit.
- `GET /api/memories/{id}` — memory fields, validated `provenance` (only conversation/message ids that still exist, plus `valid`), and the newest 100 history entries oldest-first. History snapshots omit raw source columns; the validated provenance block is the only source contract.
- `POST /api/memories/{id}/confirm` — `review` or `accepted` → `confirmed`; `last_confirmed_at` is set and the transition is written to `memory_history`. Origin, confidence, and provenance are not changed, so a confirmed inference remains labelled `inferred`.
- `POST /api/memories/{id}/reject` — only `review` → `deleted`, with the reason recorded in history. No `rejected` status exists in the schema; the soft delete guarantees exclusion from retrieval without altering CHECK constraints.
- `POST /api/memories/{id}/supersede` — `accepted`/`confirmed` → `superseded`, with an optional reason recorded in history. Used when circumstances change; the record and provenance remain inspectable but retrieval excludes them. Review candidates must be rejected instead, and terminal statuses return 409.
- `PATCH /api/memories/{id}` — statement/subject/keywords only, allowed for `accepted`/`review`/`confirmed`; the previous snapshot is appended to `memory_history` in the same transaction. Unknown fields (status, origin, confidence, ids, sources) are rejected with 422. Omitted fields keep their stored values; explicit `statement: null` or `keywords: null` is rejected with 422 before any write, while `subject: null` clears the subject and `keywords: []` clears keywords.
- `DELETE /api/memories/{id}` — idempotent soft delete (204). The row and history remain; retrieval excludes it; source conversations/messages are never deleted.

Policy/validation: ids must be 32 lowercase hex characters (422 otherwise, 404 when unknown); disallowed or repeated transitions return 409; blank/oversized/malformed corrections return 422; the empty correction body is rejected; pagination and history windows are bounded; provenance validation is batched (no N+1) and runs against real rows. Service failures return a fixed safe 500 message and memory+history writes roll back together.

Deletion/retention: this is soft deletion. No endpoint permanently erases rows, history, or backups, and the API does not claim erasure. Rejected candidates, deleted memories, and superseded/orphaned memories are excluded from both FTS5 and fallback retrieval immediately (status filters, not index surgery). `GET /api/export` is a read-only snapshot of the current local state; it does not remove anything.

Known limitations: rejection and deletion share the `deleted` status (distinguished only by the history reason); corrections of `deleted`/`superseded`/`conflicted`/`orphaned` records are rejected rather than editable; history detail is capped at 100 entries; listing order is by last update, so large stores page by activity rather than creation date; export is an on-demand snapshot (not a scheduled backup) and there is no permanent-erase endpoint.

## 10. Memory review UI status (Phase 2, Step 7, as of 2026-10-21)
Implemented in `frontend/src` on top of the §9 API; the backend and schema are unchanged.

Access and layout:
- The existing sidebar now has a `Memories` button (`aria-current` when active). It swaps the chat pane for a review screen without a new global navigation system. `Back to chat` and selecting a conversation return to chat.
- List: statement, kind, origin (`Stated by you` / `Inferred`), lifecycle badge, `No valid source` warning, and updated time. Status/kind/origin filters use the API's enums; a `Needs review` shortcut sets `status=review`. Pagination requests 20 per page with the API's `offset` and displays its real `total`; Previous/Next are disabled at the boundaries. A page that becomes empty after a mutation falls back to the last page with content.
- Detail: statement, status/origin/kind, confidence (labelled as extraction certainty, not proof), created/updated/last-confirmed timestamps, subject, keywords, validated provenance, and the newest 100 history entries (newest first). Provenance shows the conversation id with an `Open source conversation` action only when that conversation is in the loaded sidebar list; otherwise the id is shown with `(not in the current conversation list)`. Source message ids are listed as identifiers — no message content or previews are invented. An invalid provenance block warns that the memory is excluded from retrieval.
- Honesty: `review` memories are described as not used in replies until confirmed; inferred memories are labelled as possibly inaccurate and confirmation does not relabel them as user-stated; `conflicted`/`superseded`/`orphaned` are shown as not used in replies.

Actions (mirroring the §9 API policy; disallowed actions are not rendered):
- Confirm: `review`/`accepted` → `confirmed`, with `Memory confirmed.` only after the request succeeds.
- Reject: `review` only, behind an inline confirmation with an optional reason (≤500 characters). Copy states the memory becomes inactive and that the record/history remain. The result is labelled `Inactive`; a rejection reason from history is displayed, and no attempt is made to distinguish rejection from manual deletion beyond that history evidence.
- Edit: `accepted`/`review`/`confirmed` only; statement (≤2,000), optional subject (≤200), and comma-separated keywords (≤20, ≤50 chars each) with client-side validation plus server 422 handling. The draft survives failures; the form closes only after success and the detail/history refetch.
- Delete: in-app confirmation explaining the soft delete, that the stored record and history may remain, and that source conversations/messages are not deleted. The API's 204/idempotent outcome is followed by clearing the detail and refetching the list; the row remains visible as `Inactive`.
- Supersede: `accepted`/`confirmed` only, behind an inline confirmation with an optional reason (≤500 characters). Copy explains that the memory is retained for audit but excluded from replies. The result is labelled `Superseded`.
- Pending mutations disable the controls and show progress; duplicate submissions are ignored. After success the list and detail refetch (no full page reload); on 409/404 the detail also refetches so stale controls disappear, and no success message is shown before confirmation.

States and accessibility: distinct loading, unfiltered-empty, filtered-empty, list-error-with-retry, detail-error-with-retry, inline field errors, pending labels, and mutation/network error banners. Confirmation panels move focus to `Cancel` and close on Escape; controls are native buttons/selects/labels with visible focus styling. Styling reuses the existing CSS variables, responsive breakpoints, reduced-motion rules, and dark/light themes. No browser `alert()`/`confirm()` is used.

Tests and verification at the Step 7 checkpoint: `npm run build` and `npm run lint` pass; `npm test` ran 35 Vitest/jsdom tests across the API layer, memory view, and app navigation (the acceptance repair now reports 36). Browser verification used a temporary database and headless Chrome to exercise access, pagination, filters, detail/provenance, source-conversation navigation, confirm, edit validation/save, a 409 conflict, reject with reason, delete with soft-delete copy, blocked-network retry, empty state, theme toggle, focus/Escape, and console hygiene. Backend suite was 228 passed at that checkpoint (239 after the acceptance-repair tests, §11).

Known limitations: no source message previews; source-conversation navigation depends on the loaded sidebar list; rejected vs manually deleted records are distinguishable only through history; no permanent erase (export is available from the chat sidebar); no archive search, entity/story scope, embeddings, debug panel, or deep links; `deleted` records remain listable for audit by design.

## 12. Continuity, deletion, and export (Phase 3, as of 2026-10-22)

Implemented in `chat_service.py`, `memory_service.py`, `memory_retrieval.py`, `api/conversations.py`, `api/export.py`, and the frontend chat/memory components. No schema migration was required (`messages.metadata_json` and the `orphaned`/`superseded` statuses already existed).

- **Conversation deletion:** `DELETE /api/conversations/{id}` deletes the conversation and its messages in one transaction. Before deletion, memories that cite the conversation or one of its messages are transitioned to `orphaned` with the history reason “Source conversation deleted”; already-inactive statuses are left untouched. The memory row, content, and history remain, but read-time provenance invalidates the missing source and retrieval excludes the record. Backups and previously downloaded exports are not modified; the UI states this and recommends exporting first.
- **Export:** `GET /api/export` returns `format: companion-export`, version 1, persona, conversations, messages (with stored metadata), all memories including inactive/orphaned ones, memory history, counts, and a scope block that names exclusions (FTS index, migration backups, logs, previous exports). No secrets are read; the response is local-only and sets `Cache-Control: no-store` plus a download filename.
- **Superseding:** `POST /api/memories/{id}/supersede` retires an outdated `accepted`/`confirmed` memory with a reason and history entry. Corrections and source records are preserved; retrieval already excludes `superseded`.
- **Per-reply evidence:** assistant messages store `metadata.memory_evidence` — a bounded list of the memories actually supplied (id, kind, statement, origin, status, source conversation/message ids). The trace is written only from validated retrieval results, is absent when nothing was supplied, and is never added to user messages. The chat UI shows it in a disclosure under the reply.
- **Follow-up retry:** retrieval retries once with the previous user message's terms when the current message finds nothing. The regression test covers “Where does she live?” after “Where did Mara move to again?”, where merging terms raised the coverage floor and returned nothing; the fix uses the context terms alone. The retry is logged as `query_expanded` and never bypasses lifecycle/provenance/budget filters.

Verification (2026-10-22): backend suite 275 passed, 0 failed; frontend 51 tests in 4 files; production build and lint pass (0 errors, 4 advisory warnings). Disposable-database runtime checks through the real FastAPI app confirmed chat, extraction, restart persistence, cross-conversation recall, per-reply evidence, supersede/409, deletion/orphaning, export before and after deletion, and the follow-up retry. Live extraction with `qwen3.5:9b` measured 165–400s, so the default extraction timeout was raised to 600s (`MEMORY_EXTRACTION_TIMEOUT`); extraction remains best-effort and never blocks the reply.

## 11. Provenance policy (Phase 2 acceptance repair)
The single policy lives in `memory_service.resolve_source_references` and is used by both the management API and retrieval.

- **Message-bearing records:** every declared source message must still exist. When a source conversation is declared, that conversation must also exist and every referenced message must belong to it. If any declared message is missing or belongs to a different conversation, the memory is not valid grounded evidence and is excluded from retrieval. Surviving message ids are still shown by the API for audit, but stale ids are never printed as verified prompt sources.
- **Legacy conversation-only records:** a record with no message-level references is valid while its declared conversation exists. This keeps pre-existing records usable without inventing message ids.
- **Message-only records:** valid when all declared messages exist; the source conversation does not need to be the current conversation. A valid memory from conversation A therefore remains retrievable while replying in conversation B.
- **No usable source:** invalid; it is not supplied to the model, and its stored status is left unchanged (validation is read-only).
- **Multiple messages:** the conservative all-or-nothing rule applies. One deleted or mismatched message invalidates the memory for grounded evidence rather than silently narrowing its support.
- **No mutation on read:** retrieval and the API never rewrite stored ids or delete messages/conversations. A deleted source remains represented in history; validation only controls display and retrieval.

This policy is implemented and covered by regression tests for deletion, cross-conversation membership, cross-conversation retrieval, prompt filtering, API provenance, and UI warning states. The correction contract is covered by tests for explicit `null` (422, no record/history mutation), omitted fields, valid partial corrections, and old-term/new-term retrieval propagation. At the acceptance repair checkpoint the backend suite reports 239 passed, 0 failed; the frontend suite reports 36 passed across 3 files. Browser E2E was not rerun for this repair; the UI warning state is covered by Vitest, and the prior Step 7 browser verification remains documented in §10. The Step 8 acceptance audit (2026-10-22) re-ran the full suites and repeated the deletion, mismatch, multi-message, cross-conversation, and explicit-null checks through the real app on a disposable database; it made no code or schema changes and did not rerun browser E2E (no browser automation framework or driver is present).

## 13. Voice-note transcripts as memory sources (Phase 4)

Voice notes are stored separately and are **not** converted to messages or memories automatically. Their audio metadata and transcripts live in the `voice_notes` table (§12 in `ARCHITECTURE.md`), which is not searched by memory retrieval.

When the user explicitly chooses "Use transcript for memory," `POST /api/voice-notes/{id}/send-to-chat` creates one new conversation and inserts the **current** transcript (the user's correction when present) as a user message. The normal extraction task then runs on that conversation. This means:
- Provenance is ordinary message-level provenance: the transcript text is a real stored user message, so derived memories cite message ids that exist and belong to that conversation.
- The usual extraction policies apply: not every sentence becomes a memory, inferences are marked for review, sensitive/fictional content is skipped, and quotes must match the stored message.
- No assistant message is fabricated, no reply is generated, and no memory is created simply by recording or transcribing.
- Deleting the conversation later orphans derived memories under the normal rules; deleting the voice note does not touch that conversation or its memories.
- Editing the transcript after sending does not rewrite the conversation; the user must use the action again to submit the new text.

Transcription failures never affect chat, memory retrieval, or the archive, and recording/import never create memory-history entries. The voice-note UI states these boundaries explicitly.
