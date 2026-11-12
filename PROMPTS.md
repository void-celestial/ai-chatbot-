# Coding Agent Prompt Pack

Use these prompts in order. Run them in a coding agent that can inspect and edit the local project folder. Do not ask it to build every phase in one response.

## Prompt 1 — Inspect and create the specification files

You are the lead software architect for a local-first personal AI companion. Work in the currently opened project folder.

First inspect the existing folder, available runtimes, and current code. Do not delete, overwrite, or replace existing files without explaining the reason. If the folder is empty, create a `docs/` directory and create these complete documents:
- `docs/PRD.md`
- `docs/ARCHITECTURE.md`
- `docs/AI_BEHAVIOR.md`
- `docs/MEMORY_SPEC.md`
- `docs/BUILD_PLAN.md`

Use the project blueprint supplied by the user as the source of truth. The application is single-user, local-first, and for private personal conversation. It uses React + TypeScript + Vite for the UI, Python + FastAPI for the backend, SQLite for persistent storage, and the user's existing Qwen 3.5 9B model through Ollama. Verify the exact installed model tag by asking the user to run `ollama list` or by safely running it if terminal access is available; do not guess the tag. Ollama is expected at `http://localhost:11434`.

Core requirements: persistent conversations, source archive of every message, searchable memory, source-linked structured facts, separate fictional universes, characters/aliases/relationships/events/timelines, memory review/edit/delete/export, warm natural conversational behavior, honest memory recall, local-only operation by default, and clear error handling.

Do not start implementing the application in this step. Produce complete, consistent documents with no conflicting technology choices. Include data model, request lifecycle, privacy/security, testing, acceptance criteria, phased delivery, and definition of done. At the end, list created files and summarize decisions/assumptions.

## Prompt 2 — Verify the environment

Read the documents in `docs/` before acting. Inspect the current project and report:
1. Windows version if accessible
2. Node and npm versions
3. Python version
4. `ollama list` output and exact Qwen model tag
5. Whether `http://localhost:11434` responds
6. Current folder structure
7. Any missing dependency or blocker

Do not install packages or change settings yet. Do not expose private environment variables or secrets. If a command cannot be run, say so and give me the exact command to run.

## Prompt 3 — Implement Phase 1 only

Read `docs/PRD.md`, `docs/ARCHITECTURE.md`, and `docs/BUILD_PLAN.md`. Implement Phase 1 only: React + TypeScript + Vite frontend, FastAPI backend, Ollama connectivity/health checks, basic chat with streamed output if supported, and SQLite persistence for conversations/messages.

Before coding, summarize the files you plan to create/change. Preserve existing work. Use modular services, typed API responses, safe error handling, and no cloud APIs. Do not implement fake responses or placeholder buttons. Bind backend to `127.0.0.1` by default. Read the exact model tag from configuration; do not assume a tag. Add `.env.example` without secrets and a `.gitignore` that excludes local databases, backups, environment files, and generated artifacts.

After implementation, run backend tests and frontend build/type-check. Fix errors. Report exact commands and observed results. Do not claim unrun tests passed.

## Prompt 4 — Implement source archive and conversation management

Read the PRD and architecture. Implement conversation list, create/open/rename/archive/delete, stable message ordering, persistent storage, keyword search across original messages, and local export. Preserve all messages unless the user explicitly deletes them. Deleting a conversation must make the effect on linked memories explicit; do not silently erase unrelated memory records. Add tests for persistence, ordering, search, export, and delete behavior. Run the tests and frontend checks.

## Prompt 5 — Implement structured long-term memory

Read `docs/MEMORY_SPEC.md` and `docs/ARCHITECTURE.md`. Implement the structured memory schema, source message references, candidate extraction service, confidence/status fields, deduplication, conflict handling, retrieval service, and prompt-context builder.

Important: never claim perfect recall. Never create a memory without source IDs. Do not treat hypothetical or fictional content as a real-life fact. Do not silently resolve conflicting facts. Ensure deleted memories are immediately excluded from retrieval. Add a memory UI to inspect, edit, correct, pin, and delete records. Add regression tests for recall, corrections, conflicts, and deletion. Keep extraction asynchronous/separate when possible so it does not block chat. Do not add embeddings yet; finish and test keyword/structured retrieval first.

## Prompt 6 — Implement story universes and continuity

Implement story universes with characters, aliases, attributes, relationships, canon notes, and chronological events. Every story entity must be scoped to a universe. Support tentative ideas versus confirmed canon and retain source message IDs. When the same name appears in different universes, keep records separate. Add tests proving no cross-universe leakage and that a user correction updates future retrieval. Update the UI and docs. Run all checks.

## Prompt 7 — Add local semantic search

Only begin after the structured and keyword retrieval tests pass. Inspect Ollama's locally available models and ask before downloading an embedding model if one is missing. If available, use a local embedding model (for example, `embeddinggemma`) through Ollama. Do not use cloud embeddings. Store the embedding model identifier and make the index rebuildable. Combine lexical and semantic search with source-linked results, scoped filtering, and a lexical fallback if embeddings fail. Benchmark with a small test set and report measured results; do not invent metrics.

## Prompt 8 — Final audit

Audit the entire app against every acceptance criterion in `docs/PRD.md`. Inspect every interactive element. Search for TODO placeholders, fake data, inert handlers, unsafe SQL, external network calls, secrets, overly broad CORS, and claims of success without verification. Run backend tests, frontend build/type-check, and any lint checks configured. Test Ollama unavailable, model missing, empty memory, duplicate facts, conflicting facts, deletion, and separate story universes.

Fix issues found. Produce `docs/FINAL_AUDIT.md` with each criterion marked PASS, FAIL, or NOT TESTED and evidence for the status. Do not mark a criterion PASS without actually verifying it. Finish with exact startup instructions and known limitations.
