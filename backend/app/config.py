"""Application configuration.

Values come from the environment (or a `.env` file at the project root) with
defaults verified during Phase 0. The model tag is read from configuration —
never hardcoded at call sites — and can be validated against Ollama at runtime.

Path roots differ between running from source and running frozen (the packaged
.exe), so nothing mutable ever lands in the install directory or in
PyInstaller's temporary extraction directory:

* `PROJECT_ROOT` — where `.env` lives: the repo when running from source, the
  executable's own directory when frozen (read-only use).
* `BUNDLE_ROOT` — read-only resources shipped inside the bundle
  (`sys._MEIPASS` when frozen).
* `DATA_ROOT` — writable per-user data: `%LOCALAPPDATA%\\MyAIAssistant` when
  frozen, the existing `%APPDATA%\\MyAIAssistant\\data` when running from source.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


# backend/app/config.py -> app -> backend -> project root (source checkouts).
_SOURCE_ROOT = Path(__file__).resolve().parents[2]

if _is_frozen():
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))
else:
    PROJECT_ROOT = _SOURCE_ROOT
    BUNDLE_ROOT = _SOURCE_ROOT


def _user_data_root() -> Path:
    """Writable per-user directory. `APP_DATA_DIR` overrides it."""
    override = (os.environ.get("APP_DATA_DIR") or "").strip()
    if override:
        return Path(override)
    if _is_frozen():
        local = (os.environ.get("LOCALAPPDATA") or "").strip()
        base = Path(local) if local else Path.home() / "AppData" / "Local"
        return base / "MyAIAssistant"
    return Path.home() / "AppData" / "Roaming" / "MyAIAssistant" / "data"


DATA_ROOT = _user_data_root()
# Relative defaults resolve against the user data directory when frozen; a
# source checkout keeps resolving them against the repo root.
BASE_DIR = DATA_ROOT if _is_frozen() else PROJECT_ROOT


def _default_db_path() -> Path:
    """Default database path inside the user's data directory.

    Directories are created by `init_db()` when the database is opened, never
    as an import side effect.
    """
    return DATA_ROOT / "companion.db"


MAX_MESSAGE_CHARS = 20_000
MAX_PERSONA_NAME_CHARS = 50

# Voice notes (Phase 4). Audio files never enter SQLite; only metadata and the
# transcript do. Uploads are streamed to disk with a hard size cap.
MAX_VOICE_NOTE_TITLE_CHARS = 200
MAX_TRANSCRIPT_CHARS = 50_000
DEFAULT_VOICE_NOTE_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS = 300.0
MAX_PERSONA_INSTRUCTIONS_CHARS = 2_000

# Memory record limits (Phase 2 structured memory).
MAX_MEMORY_STATEMENT_CHARS = 2_000
MAX_MEMORY_SUBJECT_CHARS = 200
MAX_MEMORY_KEYWORDS = 20
MAX_MEMORY_KEYWORD_CHARS = 50
MAX_MEMORY_SOURCE_MESSAGE_REFS = 50
MAX_MEMORY_STATUS_REASON_CHARS = 500

# Memory management API (Phase 2, Step 6). Pages are bounded so a review
# client can never request an unbounded dump of the memory store.
DEFAULT_MEMORY_PAGE_SIZE = 50
MAX_MEMORY_PAGE_SIZE = 100
MAX_MEMORY_HISTORY_ENTRIES = 100

# Memory extraction (Phase 2, Step 4). Bounds the structured-memory pipeline;
# extraction is best-effort and must never block or break ordinary chat.
MAX_EXTRACTION_CANDIDATES = 12
# Measured on the reference machine (2026-10-22): one extraction call over a
# single exchange with `qwen3.5:9b` took 165-400s, so the previous 90s default
# timed out and silently saved nothing. The timeout only bounds a background
# task that never blocks chat; override with MEMORY_EXTRACTION_TIMEOUT.
DEFAULT_EXTRACTION_TIMEOUT_SECONDS = 600.0
# Foreground-priority scheduling for background extraction (measured
# 2026-10-25): Ollama serializes generation on this machine, so an in-flight
# extraction call delayed a new message's first token to 49.3s (vs 0.2s when
# idle). The scheduler cancels a running extraction when a message arrives;
# after such a preemption it waits this long for the foreground to stay quiet
# before restarting extraction, so a burst of consecutive messages defers it
# instead of repeatedly starting and killing it. 0 restarts immediately.
# Override with MEMORY_EXTRACTION_IDLE_DELAY.
DEFAULT_EXTRACTION_IDLE_DELAY_SECONDS = 3.0
DEFAULT_CONTEXT_MESSAGE_COUNT = 40
DEFAULT_CONTEXT_MAX_CHARS = 24_000
DEFAULT_NUM_CTX = 8_192

# Ollama lifecycle (measured 2026-10-25 on the reference machine).
# A cold 9B load costs ~12.5s and a cold 27B load ~14s; Ollama evicts a model
# after 5 minutes of idleness by default, so ordinary pauses between messages
# were paying that load again. `keep_alive` is sent with every generation.
DEFAULT_OLLAMA_KEEP_ALIVE = "30m"
# Hidden reasoning is never shown or stored by this app (thinking chunks are
# discarded by the chat layer), but it is generated first: on 9B it put the
# first visible token 31s out in a 34s reply. `None` keeps the model's own
# default (thinking on); `false` skips the discarded pass entirely.
DEFAULT_OLLAMA_THINK: bool | None = None
# 0 means "no cap": answer length stays model-controlled unless configured.
DEFAULT_MAX_OUTPUT_TOKENS = 0
# Reply length policy (Phase 3, measured 2026-10-26). With the untouched prompt a
# casual one-liner came back at 25-39 tokens, but a technical question produced
# 526-590 tokens (46-48s at ~11-13 tok/s on this GPU). "concise" adds a guidance
# section that asks for short, natural answers to simple messages while keeping
# full detail for technical, multi-part, and emotional ones. "default" leaves
# the prompt exactly as it was.
#
# "adaptive" (Phase 4) is the same idea with an explicit per-situation policy:
# short for small talk, warm for distress, complete for technical and complex
# questions, and the user's own length request wins over all of it. All three
# styles are prompt guidance only — none of them caps tokens or truncates a
# reply, and the default stays "default" unless explicitly configured.
DEFAULT_REPLY_STYLE = "default"
REPLY_STYLES = ("default", "concise", "adaptive")

# Memory retrieval (Phase 2, Step 5). Retrieval is local, deterministic, and
# bounded: it never sends the whole memory store to the model.
DEFAULT_MEMORY_RETRIEVAL_LIMIT = 6
DEFAULT_MEMORY_RETRIEVAL_SCAN_LIMIT = 200
DEFAULT_MEMORY_RETRIEVAL_MIN_SCORE = 2
# Prompt budget for the memory evidence block, independent of the recent-
# history budget (context_max_chars). Memories therefore cannot displace all
# recent conversation history.
DEFAULT_MEMORY_PROMPT_MAX_CHARS = 1_600
DEFAULT_MEMORY_STATEMENT_PROMPT_CHARS = 300


def env_file_candidates() -> tuple[Path, ...]:
    """`.env` files to read, most specific first (missing keys are filled in)."""
    candidates = [PROJECT_ROOT / ".env"]
    if _is_frozen():
        candidates.append(DATA_ROOT / ".env")
    return tuple(candidates)


def load_env_file() -> None:
    """Populate missing environment variables from the `.env` file(s)."""
    for env_file in env_file_candidates():
        if not env_file.is_file():
            continue
        for raw_line in env_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    # Local Ollama API. Use the IPv4 loopback literal, not "localhost":
    # Ollama binds 127.0.0.1 only, and a fresh connection to "localhost" first
    # tries ::1 (unreachable) and then falls back after a ~0.25s delay —
    # measured costing ~260-300ms on EVERY new HTTP connection on this
    # machine, which doubled time-to-first-token through the app.
    ollama_base_url: str = "http://127.0.0.1:11434"
    model_tag: str = "qwen3.5:9b"
    num_ctx: int = DEFAULT_NUM_CTX
    # Time to establish a connection to Ollama.
    connect_timeout_seconds: float = 5.0
    # Max gap between streamed chunks before considering generation hung.
    request_timeout_seconds: float = 180.0
    # Short overall timeout for health/tags lookups.
    health_timeout_seconds: float = 4.0
    # How long Ollama keeps the model resident between requests.
    ollama_keep_alive: str = DEFAULT_OLLAMA_KEEP_ALIVE
    # Send an explicit thinking flag to Ollama (None = model default).
    ollama_think: bool | None = DEFAULT_OLLAMA_THINK
    # Optional generation cap in tokens (0 = unlimited).
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    # "default" keeps the original system prompt; "concise" or "adaptive"
    # appends one length-guidance section (see DEFAULT_REPLY_STYLE).
    reply_style: str = DEFAULT_REPLY_STYLE
    cors_origins: tuple[str, ...] = ("http://localhost:5173", "http://127.0.0.1:5173")
    db_path: Path = _default_db_path()
    # Generation context budget: recent messages only, never the whole archive.
    context_message_count: int = DEFAULT_CONTEXT_MESSAGE_COUNT
    context_max_chars: int = DEFAULT_CONTEXT_MAX_CHARS
    # Extraction call timeout + candidate flood guard.
    extraction_timeout_seconds: float = DEFAULT_EXTRACTION_TIMEOUT_SECONDS
    max_extraction_candidates: int = MAX_EXTRACTION_CANDIDATES
    # Quiet time required after a foreground message preempted extraction.
    extraction_idle_delay_seconds: float = DEFAULT_EXTRACTION_IDLE_DELAY_SECONDS
    # Retrieval: max memories returned, rows scanned per query, and the
    # minimum deterministic relevance score a memory must reach.
    memory_retrieval_limit: int = DEFAULT_MEMORY_RETRIEVAL_LIMIT
    memory_retrieval_scan_limit: int = DEFAULT_MEMORY_RETRIEVAL_SCAN_LIMIT
    memory_retrieval_min_score: int = DEFAULT_MEMORY_RETRIEVAL_MIN_SCORE
    # Prompt integration budget for the retrieved-memory evidence block.
    memory_prompt_max_chars: int = DEFAULT_MEMORY_PROMPT_MAX_CHARS
    memory_statement_prompt_chars: int = DEFAULT_MEMORY_STATEMENT_PROMPT_CHARS
    # Voice notes: local storage directory and upload cap.
    voice_notes_dir: Path = BASE_DIR / "data" / "voice_notes"
    voice_note_max_bytes: int = DEFAULT_VOICE_NOTE_MAX_BYTES
    # Local transcription via the ffmpeg `whisper` filter (whisper.cpp). The
    # default file is the multilingual medium model (English + Hindi); it is
    # installed out-of-band and never downloaded by the application.
    ffmpeg_bin: str = "ffmpeg"
    whisper_model_path: Path = BASE_DIR / "data" / "models" / "ggml-medium.bin"
    whisper_language: str = "auto"
    # Audio queue handed to whisper per inference call. The filter default (3s)
    # splits utterances at arbitrary points and produced truncated transcripts
    # when measured on 2026-10-23; 20s matches ffmpeg's recommended range for
    # file input (10-20s) without adding real-time-streaming latency.
    whisper_queue_seconds: int = 20
    # Pauses at least this long separate speech regions that are transcribed
    # independently, so a note that switches language is not decoded as a single
    # language. 0 disables segmentation (one pass over the whole file).
    whisper_segment_silence_seconds: float = 0.6
    # CPU by default so transcription does not compete with Ollama for VRAM.
    whisper_use_gpu: bool = False
    transcription_timeout_seconds: float = DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS


def _resolve_db_path(raw: str) -> Path:
    if raw == "":
        raw = "data/companion.db"
    path = Path(raw)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_reply_style() -> str:
    """Validated reply style; anything unrecognized falls back to the default."""
    raw = (os.environ.get("OLLAMA_REPLY_STYLE") or "").strip().lower()
    return raw if raw in REPLY_STYLES else DEFAULT_REPLY_STYLE


def _env_optional_bool(name: str) -> bool | None:
    """Tri-state flag: unset stays unset so the caller can omit it entirely."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return None
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    load_env_file()
    cors_raw = os.environ.get("CORS_ORIGINS", "")
    cors_origins = tuple(o.strip() for o in cors_raw.split(",") if o.strip())
    return Settings(
        host=os.environ.get("APP_HOST", "127.0.0.1"),
        port=int(os.environ.get("APP_PORT", "8000")),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
        model_tag=os.environ.get("OLLAMA_MODEL_TAG", "qwen3.5:9b"),
        num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", str(DEFAULT_NUM_CTX))),
        connect_timeout_seconds=float(os.environ.get("OLLAMA_CONNECT_TIMEOUT", "5.0")),
        request_timeout_seconds=float(os.environ.get("OLLAMA_REQUEST_TIMEOUT", "180.0")),
        health_timeout_seconds=float(os.environ.get("OLLAMA_HEALTH_TIMEOUT", "4.0")),
        ollama_keep_alive=os.environ.get(
            "OLLAMA_KEEP_ALIVE", DEFAULT_OLLAMA_KEEP_ALIVE
        ).strip()
        or DEFAULT_OLLAMA_KEEP_ALIVE,
        ollama_think=_env_optional_bool("OLLAMA_THINK"),
        max_output_tokens=max(0, int(os.environ.get("OLLAMA_NUM_PREDICT", "0"))),
        reply_style=_env_reply_style(),
        cors_origins=cors_origins
        or ("http://localhost:5173", "http://127.0.0.1:5173"),
        db_path=_resolve_db_path(os.environ.get("DB_PATH", "data/companion.db")),
        extraction_timeout_seconds=float(
            os.environ.get("MEMORY_EXTRACTION_TIMEOUT", str(DEFAULT_EXTRACTION_TIMEOUT_SECONDS))
        ),
        max_extraction_candidates=int(
            os.environ.get("MEMORY_EXTRACTION_MAX_CANDIDATES", str(MAX_EXTRACTION_CANDIDATES))
        ),
        extraction_idle_delay_seconds=max(
            0.0,
            float(
                os.environ.get(
                    "MEMORY_EXTRACTION_IDLE_DELAY",
                    str(DEFAULT_EXTRACTION_IDLE_DELAY_SECONDS),
                )
            ),
        ),
        memory_retrieval_limit=int(
            os.environ.get("MEMORY_RETRIEVAL_LIMIT", str(DEFAULT_MEMORY_RETRIEVAL_LIMIT))
        ),
        memory_retrieval_scan_limit=int(
            os.environ.get("MEMORY_RETRIEVAL_SCAN_LIMIT", str(DEFAULT_MEMORY_RETRIEVAL_SCAN_LIMIT))
        ),
        memory_retrieval_min_score=int(
            os.environ.get("MEMORY_RETRIEVAL_MIN_SCORE", str(DEFAULT_MEMORY_RETRIEVAL_MIN_SCORE))
        ),
        memory_prompt_max_chars=int(
            os.environ.get("MEMORY_PROMPT_MAX_CHARS", str(DEFAULT_MEMORY_PROMPT_MAX_CHARS))
        ),
        memory_statement_prompt_chars=int(
            os.environ.get(
                "MEMORY_STATEMENT_PROMPT_CHARS", str(DEFAULT_MEMORY_STATEMENT_PROMPT_CHARS)
            )
        ),
        voice_notes_dir=_resolve_db_path(
            os.environ.get("VOICE_NOTES_DIR", "data/voice_notes")
        ),
        voice_note_max_bytes=int(
            os.environ.get("VOICE_NOTE_MAX_BYTES", str(DEFAULT_VOICE_NOTE_MAX_BYTES))
        ),
        ffmpeg_bin=os.environ.get("FFMPEG_BIN", "ffmpeg"),
        whisper_model_path=_resolve_db_path(
            os.environ.get("WHISPER_MODEL_PATH", "data/models/ggml-medium.bin")
        ),
        whisper_language=os.environ.get("WHISPER_LANGUAGE", "auto"),
        whisper_queue_seconds=int(os.environ.get("WHISPER_QUEUE", "20")),
        whisper_segment_silence_seconds=float(
            os.environ.get("WHISPER_SEGMENT_SILENCE", "0.6")
        ),
        whisper_use_gpu=_env_bool("WHISPER_USE_GPU", False),
        transcription_timeout_seconds=float(
            os.environ.get(
                "TRANSCRIPTION_TIMEOUT", str(DEFAULT_TRANSCRIPTION_TIMEOUT_SECONDS)
            )
        ),
    )
