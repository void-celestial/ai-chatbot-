# My AI Assistant — Windows desktop app

A compact, offline personal AI assistant that runs a local Ollama model (default `qwen3.5:9b`) inside a native Windows window. The React frontend is served in-process by a FastAPI + uvicorn backend; the window is driven by pywebview.

- **No browser tab, no console window** — a normal desktop window, ~420×650 px, resizable with a sensible minimum.
- **Offline once the model is pulled** — Ollama + the model are the only external runtime requirement.
- **Model is configurable without rebuilding** — set `OLLAMA_MODEL_TAG` in `.env`.

---

## Prerequisites

- [Python 3.14](https://www.python.org/downloads/) (the `.venv` here targets that version).
- [Ollama](https://ollama.com/) installed and running locally, with the model pulled:

```bash
ollama pull qwen3.5:9b
```

- Node.js (for rebuilding the frontend; not needed to run the pre-built `.exe`).

---

## Project layout

```
.
├── backend/            # FastAPI app + desktop launcher
│   ├── app/            # application code (API, services, DB, config)
│   ├── main_desktop.py   # pywebview launcher (starts the backend, opens the window)
│   └── requirements.txt
├── frontend/           # React UI (Vite + TypeScript)
│   ├── src/
│   ├── dist/          # built frontend (served by the backend at /)
│   └── package.json
├── data/              # local SQLite DB + voice notes + whisper model (gitignored)
├── scripts/           # create_shortcuts.ps1 (Desktop + Start Menu shortcuts)
├── build.cmd          # one-command frontend build + one-file exe packaging
├── MyAIAssistant.spec # the PyInstaller definition used by build.cmd
├── .env.example       # config template
├── .env               # your local overrides (created from .env.example)
├── icon.png           # 1024×1024 RGBA app icon (transparent)
├── icon.ico          # multi-size icon: 16,24,32,48,64,128,256 px
└── README.md
```

---

## Run from source (development)

```bash
# 1. Create + activate the backend venv (one time)
python -m venv .venv
.venv/Scripts/activate        # PowerShell
# or: .venv/Scripts/Activate.ps1

# 2. Install backend dependencies
.venv/Scripts/pip install -r backend/requirements.txt

# 3. Copy the config template
copy .env.example .env

# 4. (Optional) Edit .env — model tag, DB path, ports, etc.

# 5. Start the desktop app
.venv/Scripts/python -m backend.main_desktop
```

The first run copies `.env.example` → `.env` automatically if `.env` is missing.

The desktop launcher:
1. Loads config from `.env`,
2. Creates the FastAPI app (DB init, Ollama client, extraction scheduler),
3. Serves the built `frontend/dist/` at `/`,
4. Starts uvicorn on `127.0.0.1:8000` in a background thread,
5. Opens a pywebview window at `http://127.0.0.1:8000/` sized ~420×650, centered, with the app icon.

To run the web version instead (for frontend development with hot reload):

```bash
cd frontend
npm install
npm run dev            # Vite dev server on http://localhost:5173
```

and in another terminal:

```bash
.venv/Scripts/python -m backend.app.main   # uvicorn on 127.0.0.1:8000
```

---

## Build the Windows app (`.exe`)

The app ships as a **single-file** PyInstaller executable. Everything it needs at runtime — the built React app, the icon, the database schema and migrations — is inside the bundle, so the `.exe` runs from any folder with no files copied next to it.

### One command

```bat
build.cmd
```

From the repo root, `build.cmd`

1. builds the frontend (`npm run build` in `frontend/`),
2. packages it with the single spec `MyAIAssistant.spec` (one-file, GUI subsystem, no console,
   `upx` off, the exe icon embedded),
3. checks that `dist\MyAIAssistant.exe` was produced.

Requirements: a Python venv at `.venv` (`pip install -r backend/requirements.txt` plus
`pip install pyinstaller`), and Node.js for the frontend step.

### Artifact

```
dist\MyAIAssistant.exe      # one file, ~29 MB
```

Rebuilding only the exe (frontend already built):

```bat
.venv\Scripts\pyinstaller.exe --noconfirm MyAIAssistant.spec
```

### Shortcuts

```powershell
powershell -ExecutionPolicy Bypass -File scripts\create_shortcuts.ps1
```

Creates `Desktop\My AI Assistant.lnk` and a Start Menu shortcut that point at the packaged
exe with its own icon. A legacy broken `My AI Assistant.url` on the Desktop is renamed to
`.url.bak` rather than deleted.

### Where the packaged app keeps its data

| Item | Location |
|---|---|
| Database | `%LOCALAPPDATA%\MyAIAssistant\companion.db` |
| Logs | `%LOCALAPPDATA%\MyAIAssistant\logs\app.log` (1 MB, 3 rotated backups) |
| Voice notes | `%LOCALAPPDATA%\MyAIAssistant\voice_notes\` |
| Optional `.env` | next to the `.exe`, or `%LOCALAPPDATA%\MyAIAssistant\.env` |

Nothing is written into the install folder or into PyInstaller's temporary extraction
directory. A database left by an older build in `%APPDATA%\MyAIAssistant\` is imported once,
only when the new location is still empty — existing data is never overwritten.

### Notes

- Only one copy runs at a time (a named Windows mutex): a second launch tells the user the
  app is already running and exits without touching the database or the port.
- If the port is already taken, the launcher checks *who* answers `/api/health`. Its own
  backend is reported as "already running"; any other service produces a clear error naming
  the port and `APP_PORT` instead of opening a window that shows someone else's page.
- Closing the window stops the backend gracefully (uvicorn shutdown, then process exit).

---

## Configuration (`.env`)

Copy `.env.example` to `.env` and edit. Key values:

| Variable | Default | Notes |
|---|---|---|
| `APP_HOST` | `127.0.0.1` | bind address (keep loopback) |
| `APP_PORT` | `8000` | port the backend listens on |
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | local Ollama API |
| `OLLAMA_MODEL_TAG` | `qwen3.5:9b` | **change this to switch models without rebuilding** |
| `OLLAMA_NUM_CTX` | `8192` | context window |
| `OLLAMA_KEEP_ALIVE` | `30m` | how long Ollama keeps the model loaded |
| `OLLAMA_NUM_PREDICT` | `0` | 0 = no cap |
| `OLLAMA_REPLY_STYLE` | `default` | `default`/`concise`/`adaptive` |
| `DB_PATH` | `data/companion.db` | SQLite path (relative to project root) |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | frontend origins |
| `VOICE_NOTES_DIR` | `data/voice_notes` | voice note storage |
| `WHISPER_MODEL_PATH` | `data/models/ggml-medium.bin` | whisper.cpp model (not downloaded by the app) |

---

## App window

- **Size:** 420×650 px, resizable, minimum 360×520.
- **Position:** centered on screen at launch.
- **Icon:** set from `icon.ico` (window + taskbar + `.exe` resource).
- **Title:** "My AI Assistant".
- **Layout:** the sidebar is off-screen at the narrow default width; click the hamburger in the chat header to slide it in as an overlay. Chat, memory, and voice-note panes all work at this width — the composer, message bubbles, and headers tighten to fit.

---

## How the model is loaded

The Ollama client is created once when the FastAPI app starts. The model itself is pulled/loaded by Ollama on the **first request** and kept alive (`keep_alive=30m`) for subsequent messages — it is **not** reloaded per message.

---

## Troubleshooting

- **"Model not found" / Ollama errors:** make sure Ollama is running and the model tag in `.env` matches an installed model (`ollama list`).
- **"OOM" / out of memory:** a 9B model needs enough VRAM/RAM. Try a smaller tag (e.g. `qwen3.5:4b`) by editing `OLLAMA_MODEL_TAG` in `.env`.
- **Window doesn't open / WebView2 missing:** the app needs [Microsoft Edge WebView2 Runtime](https://developer.microsoft.com/en-us/microsoft-edge/webview2/). It is preinstalled on Windows 11; on Windows 10 install the runtime if the window fails to appear.
- **Config not picked up:** for the packaged app the `.env` is read from next to the `.exe` or from `%LOCALAPPDATA%\MyAIAssistant\.env`; for a source build it is the repo root.
- **Where are the logs?** `%LOCALAPPDATA%\MyAIAssistant\logs\app.log` (`app.log.1` … `app.log.3` are older ones). A source run writes `backend/app.log`.
- **"Port 8000 is already in use by another program":** something else owns the port. Close it or set `APP_PORT` in `.env`.
- **"My AI Assistant is already running":** another instance is open (or a stale one from a previous session is still shutting down). Close it and start again.

---

## Files of interest

- `backend/main_desktop.py` — the desktop launcher (pywebview + FastAPI + uvicorn).
- `backend/app/main.py` — `create_app()` wires the API, DB, Ollama client, and extraction scheduler.
- `backend/app/config.py` — reads `.env` into a `Settings` dataclass; model tag is configurable here.
- `frontend/src/styles.css` — dark/light theme; responsive overrides for the 420px window are at the bottom.
- `frontend/src/App.tsx` — sidebar toggle state (`isSidebarOpen` defaults to `false`).
