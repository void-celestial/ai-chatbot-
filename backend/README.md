Windows desktop app (.exe)

Build the desktop .exe from the repository root, not from this folder:

```bat
build.cmd
```

`build.cmd` builds the frontend and then packages `dist\MyAIAssistant.exe` from the single
spec `..\MyAIAssistant.spec` (PyInstaller one-file, GUI subsystem, no console). The spec
bundles the built frontend, `icon.ico`, `app/db/schema.sql`, and `app/db/migrations/`, so the
exe needs no files beside it. There is no second spec and no manual copy step.

Frontend already built? Repackage only:

```bat
.venv\Scripts\pyinstaller.exe --noconfirm MyAIAssistant.spec
```

Runtime behaviour

- `main_desktop.py` starts the FastAPI backend in a background thread, waits until `/api/health`
  answers with this application's payload, then opens a 440x700 pywebview window.
- Only one instance runs at a time (named Windows mutex). A second launch exits immediately
  without opening the database or binding the port.
- If the configured port is taken, the launcher verifies who answers before showing a window:
  its own backend means "already running", any other service means a clear port error.
- Closing the window stops uvicorn gracefully, then exits.

Built artifacts and data locations

- One-file Windows .exe: `dist/MyAIAssistant.exe` (GUI, no console)
- App icon: `icon.ico` (blue chatbot-robot face, from icon.svg), embedded in the exe
- Window: 440x700, min 380x560, centered on screen (pywebview supports no maximum size)
- Logs: `%LOCALAPPDATA%\MyAIAssistant\logs\app.log` (1 MB, 3 rotated backups)
- Database: `%LOCALAPPDATA%\MyAIAssistant\companion.db`
- Voice notes: `%LOCALAPPDATA%\MyAIAssistant\voice_notes\`
- Optional config: `.env` next to the exe, or `%LOCALAPPDATA%\MyAIAssistant\.env`
- A source run (`.venv\Scripts\python backend\main_desktop.py`) keeps using the repo: database
  from `.env` (`data/companion.db` by default) and `backend/app.log`.

Note: the .exe is a GUI app with no console window. To diagnose a launch, open the log file
above; the launcher logs resolved paths, settings, backend startup and shutdown.

Note: the .exe expects Ollama to be running locally (ollama serve) with the configured model
pulled. If Ollama is not running, chat surfaces a clear in-UI error instead of freezing.
