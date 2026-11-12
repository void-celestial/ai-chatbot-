# Phase 0 — Environment Report

Date of inspection: 2026-10-20. All results below were observed directly in the terminal; nothing is assumed.

## 1. Workspace inspection

The project workspace contained no application code and no git repository. It contained seven Markdown files at the root, which are a complete project blueprint:

- `README.md` — product blueprint and principles
- `PROMPTS.md` — the phased coding-agent prompt pack
- `PRD.md`, `ARCHITECTURE.md`, `AI_BEHAVIOR.md`, `MEMORY_SPEC.md`, `BUILD_PLAN.md` — the specification set

All existing work was preserved. The five specification files were moved unchanged into `docs/` to match the repository layout defined in `docs/ARCHITECTURE.md`. `README.md` and `PROMPTS.md` remain at the root.

## 2. Verified runtime environment

| Component | Result |
|---|---|
| OS | Windows 11 (build 10.0.26300.9711) |
| Node.js | v24.19.0 |
| npm | 11.17.0 |
| Python | 3.14.6 (64-bit), only via the `py` launcher — `py -0` lists `3.14[-64]` |
| `python` / `python3` aliases | Not usable: Microsoft Store stub intercepts them |
| Git | 2.55.0.windows.3 |
| Ollama | 0.40.6 |
| Ollama API | `http://localhost:11434` reachable; `/api/tags` returned the model list |

## 3. Verified Ollama model

```
NAME          ID              SIZE      MODIFIED
qwen3.5:9b    6488c96fa5fa    6.6 GB    5 weeks ago
```

Details from `/api/tags`: parameter_size 9.7B, quantization Q4_K_M, context_length 262144, embedding_length 4096, family qwen35, capabilities completion/vision/tools/thinking.

This is the exact installed tag: `qwen3.5:9b`. The backend must still read the tag from configuration (`.env` / settings) rather than hardcoding it, and must validate the configured tag against `/api/tags` at startup and in diagnostics.

No embedding model is installed. Phase 7 (semantic retrieval) needs one (for example `embeddinggemma`) and must ask the user before any download.

## 4. Suitability for the intended stack

- Python 3.14.6 satisfies FastAPI's requirements; the `py` launcher (not `python`) must be used for backend commands.
- Node 24 and npm 11 support Vite's current versions.
- The GPU/RAM budget (RTX 4050, 16 GB) is adequate for the 6.6 GB Q4_K_M model; the 262k context length exceeds what the app will use, so the backend must apply its own context budget per `docs/MEMORY_SPEC.md`.

## 5. Blockers

None. No external accounts, cloud APIs, or paid services are needed. Nothing was installed, downloaded, deleted, or reconfigured during inspection.

## 6. Next phase

Phase 1 — Working local chat (per `docs/BUILD_PLAN.md`): Vite frontend, FastAPI backend, Ollama health checks, streamed chat, SQLite persistence for conversations and messages. Awaiting user review before starting.
