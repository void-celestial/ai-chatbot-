# -*- mode: python ; coding: utf-8 -*-
"""Single packaging definition for My AI Assistant (one-file Windows build).

Inputs (all relative to this spec):
  backend/main_desktop.py        launcher entry point
  frontend/dist/                 built React app, served from `sys._MEIPASS`
  backend/app/db/schema.sql      read at runtime via `Path(__file__).with_name`
  backend/app/db/migrations/     read at runtime the same way
  icon.ico                       window/exe icon, resolved from the bundle

Only those runtime files are bundled: the rest of `backend/` (source, specs,
logs, __pycache__) is compiled into the archive and never shipped as data.
Mutable state (database, logs, voice notes) lives in %LOCALAPPDATA%.

Build it from the repo root with build.cmd.
"""
import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

REPO = Path(globals().get("SPECPATH") or os.getcwd()).resolve()
FRONTEND_DIST = REPO / "frontend" / "dist"
DB_DIR = REPO / "backend" / "app" / "db"
MIGRATIONS_DIR = DB_DIR / "migrations"
ICON = REPO / "icon.ico"

for required, label in (
    (FRONTEND_DIST / "index.html", "frontend build (frontend/dist/index.html)"),
    (DB_DIR / "schema.sql", "database schema (backend/app/db/schema.sql)"),
    (MIGRATIONS_DIR, "database migrations (backend/app/db/migrations)"),
    (ICON, "application icon (icon.ico)"),
):
    if not required.exists():
        raise SystemExit(
            f"Missing {label}: {required}\n"
            "From the repo root run build.cmd, which builds the frontend first."
        )

datas = [
    (str(FRONTEND_DIST), "frontend/dist"),
    (str(ICON), "."),
    (str(DB_DIR / "schema.sql"), "backend/app/db"),
    (str(MIGRATIONS_DIR), "backend/app/db/migrations"),
]
binaries = []
hiddenimports = [
    "webview",
    "fastapi.staticfiles",
    "starlette.staticfiles",
    "uvicorn",
    "uvicorn.config",
    "uvicorn.server",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.middleware",
    "uvicorn.middleware.proxy_headers",
    "uvicorn.middleware.wsgi",
    "uvicorn.importer",
    "uvicorn.logging",
    "starlette.middleware",
    "starlette.middleware.base",
    "starlette.middleware.exceptions",
    "starlette.middleware.gzip",
    "starlette.middleware.trustedhost",
    "starlette.middleware.cors",
    "sqlite3",
]

# uvicorn imports its loop/http/lifespan implementations dynamically.
collected = collect_all("uvicorn")
datas += collected[0]
binaries += collected[1]
hiddenimports += collected[2]


a = Analysis(
    [str(REPO / "backend" / "main_desktop.py")],
    pathex=[str(REPO)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="MyAIAssistant",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON),
)
