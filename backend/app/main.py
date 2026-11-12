import logging

from functools import partial

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api import chat, conversations, export, health, memories, persona, voice_notes
from .api.chat import run_memory_extraction
from .db.connection import init_db
from .db.migration import migrate
from .config import Settings, load_settings
from .services.extraction_scheduler import ExtractionScheduler
from .services.ollama_client import OllamaClient
from .services.transcription import (
    ERROR_EMPTY,
    TranscriptionEngine,
    TranscriptionFailure,
    TranscriptionOutcome,
)
from .services.voice_notes import recover_interrupted_transcriptions


class _NullTranscription:
    """No-op transcription engine used when ffmpeg/whisper is unavailable."""

    def availability(self, settings: object) -> str | None:
        return None

    async def transcribe(self, settings: object, audio_path: Path) -> TranscriptionOutcome:
        raise TranscriptionFailure(ERROR_EMPTY, "transcription_disabled")

logger = logging.getLogger("companion")


def _configure_logging() -> None:
    """Make this app's INFO logs visible under plain `uvicorn ...`.

    Uvicorn configures only its own loggers, so the app's `logger.info(...)`
    calls (retrieval counts, extraction outcomes, and the per-reply generation
    timing line) were silently dropped on the documented run command. These
    lines are metadata only — counts, ids, and durations, never message or
    memory text. An existing root handler (pytest, a custom launcher) is left
    untouched.
    """
    if logging.getLogger().handlers:
        return
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx logs one INFO line per request, which would put an Ollama call log
    # line next to every chat turn for no diagnostic value.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def create_app(
    settings: Settings | None = None,
    ollama: OllamaClient | None = None,
    transcription: TranscriptionEngine | None = None,
) -> FastAPI:
    """Create the FastAPI application.

    `settings`, `ollama`, and `transcription` are injectable so tests can point
    the database at a temporary file and swap in fake local engines.
    """
    _configure_logging()
    resolved_settings = settings or load_settings()
    app = FastAPI(title="Personal AI Companion", version="0.1.0")
    app.state.settings = resolved_settings
    app.state.ollama = ollama or OllamaClient(resolved_settings)
    app.state.transcription = transcription or _NullTranscription()
    # Foreground-priority extraction: chat endpoints mark their turns around
    # this scheduler, which runs extraction only while the model is idle.
    app.state.extraction_scheduler = ExtractionScheduler(
        job=partial(run_memory_extraction, resolved_settings, app.state.ollama),
        idle_delay_seconds=resolved_settings.extraction_idle_delay_seconds,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
    )

    # Routers
    app.include_router(health.router)
    app.include_router(conversations.router)
    app.include_router(chat.router)
    app.include_router(persona.router)
    app.include_router(memories.router)
    app.include_router(export.router)
    app.include_router(voice_notes.router)

    # Ensure the local database exists and is at the current schema version.
    init_db(resolved_settings.db_path)
    schema_version = migrate(resolved_settings.db_path)
    # Voice-note transcription is an optional Phase 4 feature. This desktop
    # build ships the chat + voice-notes metadata surface without installing
    # ffmpeg/whisper.cpp, so we leave transcription engines as a no-op rather
    # than failing on import at startup.
    if resolved_settings.voice_notes_dir:
        recover_interrupted_transcriptions(resolved_settings.db_path)
    logger.info(
        "Companion backend initialised (db=%s schema=v%s, model=%s, ollama=%s)",
        resolved_settings.db_path,
        schema_version,
        resolved_settings.model_tag,
        resolved_settings.ollama_base_url,
    )
    return app


# The application is built on first access, not on import.
#
# `import backend.app.main` used to create an app as a side effect: it opened
# the database, ran migrations, and recovered interrupted transcriptions. Every
# importer paid that cost, and the callers that build their own app paid it
# twice — the tests import `create_app`, and the desktop launcher imports it to
# build the app from the settings it loaded (`main_desktop.py`). That produced
# two live applications and two migrations per launch, and made a test run
# write to the real local database. Resolving `app` lazily gives uvicorn's
# `backend.app.main:app` target the single intended instance while leaving
# plain imports side-effect free.
_app: FastAPI | None = None


def __getattr__(name: str) -> FastAPI:
    if name != "app":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    global _app
    if _app is None:
        _app = create_app()
    return _app


if __name__ == "__main__":
    import uvicorn

    server_app = create_app()
    settings: Settings = server_app.state.settings
    uvicorn.run(
        server_app,
        host=settings.host,
        port=settings.port,
        log_level="info",
    )
