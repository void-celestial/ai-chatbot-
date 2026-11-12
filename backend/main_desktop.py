"""My AI Assistant — desktop launcher (pywebview).

Starts the FastAPI backend in a background thread, waits for the backend's own
/api/health to answer, then shows the built React frontend in a small native
window. Closing the window stops the backend and then the process.

Run from source (dev) or from the frozen .exe (production). The two modes use
different path roots:

* the frontend and icon come from the bundle (`sys._MEIPASS` when frozen,
  the repo when running from source) so a standalone .exe needs no files
  next to it;
* the database, logs, and voice-note files live in the per-user data
  directory (`%LOCALAPPDATA%\\MyAIAssistant` when frozen) so nothing is ever
  written into the install directory or into the temporary extraction
  directory.

Logs go to ``<data dir>\\logs\\app.log`` (rotating, 1 MB x 3). Set
MYAIASSISTANT_LOG_TO_STDOUT=1 to additionally mirror log lines to stderr and
suppress modal error dialogs, which is what the headless local checks use.
"""
from __future__ import annotations

import ctypes
import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
from io import StringIO
from pathlib import Path
from urllib.request import Request, urlopen

import webview

APP_NAME = "MyAIAssistant"
WINDOW_TITLE = "My AI Assistant"
# One instance per Windows session (Local\ namespace). Held for the whole
# process lifetime; created before anything opens the database.
MUTEX_NAME = "Local\\MyAIAssistantDesktop"

LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 3

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_PORT_UNAVAILABLE = 2

HERE = Path(__file__).resolve().parent
_REPO_ROOT = HERE.parent

# Running this file as a script puts `backend/` on sys.path, not the repo root,
# so the `backend.app` imports below would fail. Frozen builds get their
# modules from the bundle instead.
if not getattr(sys, "frozen", False):
    _repo_path = str(_REPO_ROOT)
    if _repo_path not in sys.path:
        sys.path.insert(0, _repo_path)

# Headless-test visibility: when MYAIASSISTANT_LOG_TO_STDOUT is set we also
# mirror log lines to stderr. The mirror is buffered only by the OS, not by
# Python's usual block buffering, so a short-lived test observer can read it.
# Modal dialogs are suppressed in that mode so an unattended run cannot block.
_HEADLESS = bool(os.environ.get("MYAIASSISTANT_LOG_TO_STDOUT"))
if _HEADLESS:
    try:
        unbuffered = os.fdopen(os.dup(sys.stderr.fileno()), "w", buffering=1)
        _STDERR_MIRROR = unbuffered
    except Exception:
        _STDERR_MIRROR = None
else:
    _STDERR_MIRROR = None

_LOG_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# paths
# --------------------------------------------------------------------------- #
def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _bundle_root() -> Path:
    """Read-only resources shipped with the app (frontend, icon, .env.example)."""
    if _is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return _REPO_ROOT


def _install_root() -> Path:
    """Directory holding the .exe (frozen) or the repo root (source)."""
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return _REPO_ROOT


def _data_root() -> Path:
    """Writable per-user directory: DB, logs, voice notes."""
    if not _is_frozen():
        return _REPO_ROOT
    override = (os.environ.get("APP_DATA_DIR") or "").strip()
    if override:
        return Path(override)
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    base = Path(local) if local else Path.home() / "AppData" / "Local"
    return base / APP_NAME


def _log_path() -> Path:
    if _is_frozen():
        return _data_root() / "logs" / "app.log"
    # Source mode keeps the historical location (backend/app.log).
    return HERE / "app.log"


def _rotate_log(path: Path) -> None:
    try:
        if not path.exists() or path.stat().st_size < LOG_MAX_BYTES:
            return
        for index in range(LOG_BACKUPS - 1, 0, -1):
            src = path.with_name(f"{path.name}.{index}")
            if src.exists():
                os.replace(src, path.with_name(f"{path.name}.{index + 1}"))
        os.replace(path, path.with_name(f"{path.name}.1"))
    except Exception:
        # Rotation is best-effort; never lose the process over it.
        pass


def _append(text: str) -> None:
    """Write one line to the log file and the stderr mirror, if present."""
    if _STDERR_MIRROR is not None:
        try:
            _STDERR_MIRROR.write(text + "\n")
            _STDERR_MIRROR.flush()
        except Exception:
            pass
    path = _log_path()
    with _LOG_LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            _rotate_log(path)
            with path.open("a", encoding="utf-8") as f:
                f.write(text + "\n")
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except Exception:
                    pass
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #
class _LogFile(StringIO):
    def write(self, s: str) -> int:
        super().write(s)
        _append(s.rstrip("\n"))
        return len(s)

    def flush(self) -> None:
        pass


def _configure_root_logger(level: int) -> None:
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(_LogFile())
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s %(message)s")
        )
        root.addHandler(handler)
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
# single instance
# --------------------------------------------------------------------------- #
_mutex_handle: object | None = None


def _acquire_single_instance() -> bool:
    """Create the per-session mutex. Returns False when another copy owns it.

    Called before any database access or backend startup so a second launch can
    never touch the database or bind the port. If the mutex API is unavailable
    the launch is allowed to continue (a missing guard must not block startup).
    """
    global _mutex_handle
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_wchar_p,
        ]
        handle = kernel32.CreateMutexW(None, 0, MUTEX_NAME)
        if not handle:
            return True
        already_exists = kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS
        if already_exists:
            # Release our reference so holding it here cannot keep the mutex
            # alive and block a later launch after the other process exits.
            kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel32.CloseHandle(ctypes.c_void_p(handle))
            return False
        _mutex_handle = handle
        return True
    except Exception:
        return True


# --------------------------------------------------------------------------- #
# backend identity / ports
# --------------------------------------------------------------------------- #
def _health_url(host: str, port: int) -> str:
    return f"http://{host}:{port}/api/health"


# /api/health itself waits on Ollama, so it can legitimately take a couple of
# seconds (measured 2.05s with Ollama unreachable). The probes must outwait the
# backend's own health timeout, or a healthy-but-degraded backend looks dead.
_PROBE_TIMEOUT = 5.0


def _probe_health(host: str, port: int, timeout: float = _PROBE_TIMEOUT) -> dict | None:
    """GET /api/health; the parsed payload, or None when nothing answers."""
    try:
        request = Request(
            _health_url(host, port), headers={"User-Agent": "MyAIAssistant/health"}
        )
        with urlopen(request, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _is_our_backend(payload: dict | None, model_tag: str) -> bool:
    """True only for a health payload produced by this application.

    An unrelated service that happens to listen on the same port (or a stale
    process from another app) will not match this shape, so it is never
    mistaken for our backend.
    """
    if not isinstance(payload, dict):
        return False
    if payload.get("status") not in ("ok", "degraded"):
        return False
    ollama = payload.get("ollama")
    model = payload.get("model")
    return (
        isinstance(ollama, dict)
        and isinstance(ollama.get("reachable"), bool)
        and isinstance(model, dict)
        and model.get("tag") == model_tag
        and isinstance(model.get("available"), bool)
    )


def _is_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex((host, port)) == 0


def _can_bind(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _port_state(
    host: str, port: int, model_tag: str, probe_timeout: float = _PROBE_TIMEOUT
) -> tuple[str, dict | None]:
    """Classify the port: "free", "ours" (this app's backend) or "foreign"."""
    payload = _probe_health(host, port, timeout=probe_timeout)
    if payload is not None:
        return ("ours" if _is_our_backend(payload, model_tag) else "foreign"), payload
    if _is_listening(host, port):
        return "foreign", None
    if not _can_bind(host, port):
        return "foreign", None
    return "free", None


# --------------------------------------------------------------------------- #
# resources
# --------------------------------------------------------------------------- #
def _find_frontend_dist() -> Path:
    candidates: list[Path] = []
    if _is_frozen():
        candidates.append(_bundle_root() / "frontend" / "dist")
    candidates.append(_install_root() / "frontend" / "dist")
    candidates.append(_install_root() / "dist")
    for candidate in candidates:
        if candidate.is_dir() and (candidate / "index.html").is_file():
            return candidate
    tried = "\n".join(f"  - {c}" for c in candidates)
    raise RuntimeError(
        "Built frontend not found. Expected index.html in one of:\n"
        f"{tried}\n"
        "Rebuild with build.cmd (it runs the frontend build before packaging)."
    )


def _find_icon() -> Path | None:
    candidates = [_bundle_root() / "icon.ico", _install_root() / "icon.ico"]
    if not _is_frozen():
        candidates.append(_REPO_ROOT / "icon.ico")
    for path in candidates:
        if path.is_file():
            return path
    return None


def _find_env_file() -> Path | None:
    for path in (_install_root() / ".env", _data_root() / ".env"):
        if path.is_file():
            return path
    return None


# --------------------------------------------------------------------------- #
# backend lifecycle
# --------------------------------------------------------------------------- #
def _wait_for_ready(
    host: str,
    port: int,
    model_tag: str,
    logger: logging.Logger,
    errors: list[str],
    timeout: float = 60.0,
    probe_timeout: float = _PROBE_TIMEOUT,
) -> dict:
    """Poll the port until *our* backend answers health."""
    url = _health_url(host, port)
    deadline = time.monotonic() + timeout
    attempt = 0
    while time.monotonic() < deadline:
        if errors:
            raise RuntimeError(f"backend server thread failed: {errors[0]}")
        attempt += 1
        payload = _probe_health(host, port, timeout=probe_timeout)
        if payload is not None:
            if _is_our_backend(payload, model_tag):
                logger.info("backend ready: %s", json.dumps(payload)[:200])
                return payload
            raise RuntimeError(
                f"{url} answered, but not with this application's health payload: "
                f"{json.dumps(payload)[:200]}"
            )
        time.sleep(0.25)
    if errors:
        raise RuntimeError(f"backend server thread failed: {errors[0]}")
    raise RuntimeError(
        f"Backend did not become ready at {url} within {timeout}s "
        f"({attempt} attempts). See the log for details."
    )


def _stop_backend(server: object, thread: threading.Thread, logger: logging.Logger) -> None:
    if thread is None or not thread.is_alive():
        logger.info("backend thread already stopped")
        return
    logger.info("requesting backend shutdown")
    try:
        setattr(server, "should_exit", True)
    except Exception as exc:
        logger.warning("could not set should_exit: %s", exc)
    thread.join(timeout=5.0)
    if thread.is_alive():
        logger.warning("backend thread still running 5s after shutdown request")
    else:
        logger.info("backend stopped")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main() -> int:
    # 1. Single instance first: a duplicate launch must not open the database,
    #    start a second backend, or bind the port.
    if not _acquire_single_instance():
        _notify(
            "My AI Assistant is already running.",
            "My AI Assistant",
        )
        return EXIT_OK

    level = logging.DEBUG if "--debug" in sys.argv else logging.INFO
    _configure_root_logger(level)
    logger = logging.getLogger("main_desktop")

    frozen = _is_frozen()
    logger.info("=== %s desktop launcher ===", WINDOW_TITLE)
    logger.info(
        "frozen=%s exe=%s py=%s.%s",
        frozen,
        sys.executable,
        sys.version_info.major,
        sys.version_info.minor,
    )
    logger.info("bundle_root=%s", _bundle_root())
    logger.info("install_root=%s", _install_root())
    logger.info("data_root=%s", _data_root())
    logger.info("log=%s", _log_path())

    env_file = _find_env_file()
    if env_file is None:
        logger.info("no .env found next to the exe or in the data dir; using built-in defaults")

    from backend.app import config as app_config

    app_config.load_env_file()
    if env_file is not None:
        logger.info(".env loaded from %s", env_file)

    for key in (
        "APP_HOST",
        "APP_PORT",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL_TAG",
        "DB_PATH",
        "APP_DATA_DIR",
    ):
        if key in os.environ:
            logger.info("env[%s]=%s", key, os.environ[key])

    # Keep the database in the writable user data directory, never next to
    # the exe or inside the extraction directory.
    os.environ.setdefault("DB_PATH", str(app_config.DATA_ROOT / "companion.db"))
    if frozen:
        _import_legacy_database(logger, app_config.DATA_ROOT / "companion.db")

    settings = app_config.load_settings()
    logger.info(
        "settings: host=%s port=%s model=%s ollama=%s db=%s voice_notes=%s",
        settings.host,
        settings.port,
        settings.model_tag,
        settings.ollama_base_url,
        settings.db_path,
        settings.voice_notes_dir,
    )

    # 2. Port ownership: verify who answers before building the app, so an
    #    unrelated service or another backend is never taken for ours.
    probe_timeout = max(_PROBE_TIMEOUT, settings.health_timeout_seconds + 1.5)
    state, payload = _port_state(
        settings.host, settings.port, settings.model_tag, probe_timeout=probe_timeout
    )
    if state == "ours":
        message = (
            f"My AI Assistant is already running on port {settings.port}.\n\n"
            "Close the other window (or the background process from a previous "
            "session) and start it again."
        )
        logger.warning("port %s is already served by this application: %s", settings.port, payload)
        _notify(message, WINDOW_TITLE)
        return EXIT_OK
    if state == "foreign":
        message = (
            f"Port {settings.port} is already in use by another program.\n\n"
            "Close that program, or choose a different port by setting APP_PORT "
            "in the .env file next to the executable (or in "
            f"{_data_root()})."
        )
        logger.error("port %s is in use by something that is not this application", settings.port)
        _notify(message, f"{WINDOW_TITLE} - Launch Error")
        return EXIT_PORT_UNAVAILABLE
    logger.info("port %s is free", settings.port)

    try:
        frontend_dist = _find_frontend_dist()
    except RuntimeError as exc:
        logger.error("%s", exc)
        _fatal(str(exc))
        return EXIT_FATAL
    icon_path = _find_icon()
    logger.info("frontend_dist=%s icon=%s", frontend_dist, icon_path)

    from backend.app.main import create_app

    app = create_app(settings=settings)
    logger.info("create_app() OK")

    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
    logger.info("staticfiles mounted at / from %s", frontend_dist)

    import asyncio
    import uvicorn

    uvicorn_log_level = "warning" if level > logging.DEBUG else "info"
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        log_level=uvicorn_log_level,
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    server_errors: list[str] = []

    def _run_server() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(server.serve())
        except BaseException as exc:  # noqa: BLE001 - reported to the launcher
            message = f"{type(exc).__name__}: {exc}"
            server_errors.append(message)
            logger.error("backend server thread failed: %s", message)
            logger.debug("%s", traceback.format_exc())
        finally:
            logger.info("backend server thread exiting")

    thread = threading.Thread(target=_run_server, name="uvicorn", daemon=True)
    thread.start()
    logger.info("uvicorn thread started")

    try:
        _wait_for_ready(
            settings.host,
            settings.port,
            settings.model_tag,
            logger,
            server_errors,
            probe_timeout=probe_timeout,
        )
    except Exception as exc:
        logger.error("backend did not start: %s", exc)
        _stop_backend(server, thread, logger)
        _fatal(
            f"The backend could not start on port {settings.port}.\n\n{exc}\n\n"
            f"See the log for details:\n{_log_path()}"
        )
        return EXIT_FATAL

    api_url = f"http://{settings.host}:{settings.port}/"
    # pywebview takes no window icon argument: the icon is the executable's
    # embedded resource (bundled by the spec). `_find_icon()` only verifies it
    # shipped, and there is no maximum-size argument either.
    logger.info(
        "launching window: url=%s title=%s bundled_icon=%s window=440x700 min=380x560",
        api_url,
        WINDOW_TITLE,
        icon_path is not None,
    )

    window = webview.create_window(
        WINDOW_TITLE,
        api_url,
        width=440,
        height=700,
        resizable=True,
        min_size=(380, 560),
        text_select=False,
    )

    _center_window(window, logger)

    def _on_closed() -> None:
        logger.info("window closed -> stopping backend")
        _stop_backend(server, thread, logger)
        logger.info("=== shutdown complete ===")
        sys.exit(EXIT_OK)

    window.events.closed += _on_closed
    try:
        webview.start(debug=level == logging.DEBUG)
    except Exception as exc:
        logger.error("webview.start failed: %s", exc)
        _stop_backend(server, thread, logger)
        _fatal(
            f"The window could not be opened.\n\n{exc}\n\n"
            f"See the log for details:\n{_log_path()}"
        )
        return EXIT_FATAL
    finally:
        logger.info("webview.start() returned")

    _stop_backend(server, thread, logger)
    logger.info("=== shutdown complete ===")
    return EXIT_OK


def _import_legacy_database(logger: logging.Logger, target: Path) -> None:
    """One-time, non-destructive import of a database from an older build.

    Copies a pre-existing database into the current data directory only when
    the target does not exist yet; it never overwrites user data.
    """
    if target.exists():
        return
    roaming = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
    for legacy in (
        roaming / APP_NAME / "companion.db",
        roaming / APP_NAME / "data" / "companion.db",
    ):
        if legacy.is_file():
            try:
                import shutil

                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(legacy), str(target))
                logger.info("imported legacy database %s -> %s", legacy, target)
            except Exception as exc:
                logger.warning("could not import legacy database %s: %s", legacy, exc)
            return


def _center_window(window: webview.window.Window, logger: logging.Logger) -> None:
    try:
        width = window.width
        height = window.height
        sw = window.get_screen_size()
        window.move((sw.width - width) // 2, (sw.height - height) // 2)
    except Exception as exc:
        logger.debug("window centering skipped: %s", exc)


def _notify(message: str, title: str) -> None:
    """Show a message to the user; in headless mode just log it."""
    _append(f"{title}: {message}")
    if _HEADLESS or os.name != "nt":
        return
    try:
        ctypes.windll.user32.MessageBoxW(None, message, title, 0x1000)
    except Exception as exc:
        _append(f"MessageBox failed: {exc}")


def _fatal(message: str) -> None:
    _append(f"FATAL:\n{message}")
    _notify(message, f"{WINDOW_TITLE} - Launch Error")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception:
        tb = traceback.format_exc()
        _append(f"FATAL:\n{tb}")
        _notify(
            f"Unexpected launch failure.\n\nSee the log for details:\n{_log_path()}\n\n---\n{tb}",
            f"{WINDOW_TITLE} - Launch Error",
        )
        sys.exit(EXIT_FATAL)
