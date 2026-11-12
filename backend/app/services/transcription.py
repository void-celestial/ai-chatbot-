"""Local speech-to-text via the ffmpeg `whisper` filter (whisper.cpp).

The installed ffmpeg already links whisper.cpp, so no Python package or
separate binary is required. The user supplies a GGML model file; this module
never downloads one and never calls a network service.

whisper.cpp decodes one queued audio chunk in a single language. A chunk that
spans a language switch therefore mangles one of the languages (measured on
2026-10-23: an English+Hindi note decoded as Urdu script plus dropped Hindi
audio). This module splits the recording at speech pauses, so each chunk is
speech from roughly one language, then joins the per-segment transcripts.
Recordings without usable pauses are decoded in one pass exactly as before.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

# Safe, user-facing reason codes stored on the note.
ERROR_FFMPEG_MISSING = "ffmpeg_missing"
ERROR_MODEL_MISSING = "model_missing"
ERROR_MODEL_PATH_LAYOUT = "model_path_not_relocatable"
ERROR_TIMEOUT = "timeout"
ERROR_ENGINE_FAILED = "engine_failed"
ERROR_EMPTY = "empty_transcript"
ERROR_INVALID_OUTPUT = "invalid_output"

# Pause-segmentation bounds. More regions than this would mean one model load
# per region for little accuracy gain, so long multi-pause notes stay single-pass.
MAX_SPEECH_REGIONS = 6
MIN_SPEECH_REGION_SECONDS = 0.5
SEGMENT_PADDING_SECONDS = 0.1
# Silence threshold relative to the recording's own peak, so quiet microphones
# are not treated as entirely silent.
SILENCE_RANGE_BELOW_PEAK_DB = 30.0
MIN_SILENCE_THRESHOLD_DB = -70.0
MAX_SILENCE_THRESHOLD_DB = -25.0

_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?\d+(?:\.\d+)?)\s*dB")
_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


class TranscriptionFailure(Exception):
    """A transcription attempt failed with a safe reason code."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass(frozen=True)
class TranscriptionOutcome:
    text: str
    language: str | None = None


class TranscriptionEngine(Protocol):
    def availability(self, settings: object) -> str | None:
        """Return a reason code when unavailable, else None."""

    async def transcribe(self, settings: object, audio_path: Path) -> TranscriptionOutcome:
        ...


def _escape_filter_value(value: str) -> str:
    """Escape a value for a filtergraph option.

    The graph is parsed twice, so Windows drive colons are not supported here;
    callers pass paths relative to the working directory instead.
    """
    escaped = value.replace("\\", "/")
    for char in ("'", "[", "]", ",", ";"):
        escaped = escaped.replace(char, "\\" + char)
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace(" ", "\\ ")
    return escaped


def _looks_like_path(value: str) -> bool:
    return os.path.sep in value or bool(Path(value).drive)


def _parse_ffmpeg_duration(stderr: str) -> float | None:
    match = _DURATION_RE.search(stderr)
    if match is None:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _parse_max_volume(stderr: str) -> float | None:
    match = _MAX_VOLUME_RE.search(stderr)
    return float(match.group(1)) if match else None


def _parse_silences(stderr: str, duration: float) -> list[tuple[float, float]]:
    """Pair `silence_start`/`silence_end` lines; a trailing silence runs to EOF."""
    silences: list[tuple[float, float]] = []
    pending: float | None = None
    for line in stderr.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match is not None:
            pending = max(0.0, float(start_match.group(1)))
            continue
        end_match = _SILENCE_END_RE.search(line)
        if end_match is not None and pending is not None:
            end = min(duration, float(end_match.group(1)))
            if end > pending:
                silences.append((pending, end))
            pending = None
    if pending is not None and duration > pending:
        silences.append((pending, duration))
    return silences


def _speech_regions(
    silences: list[tuple[float, float]],
    duration: float,
    min_silence: float,
) -> list[tuple[float, float]]:
    """Turn silence intervals into padded speech regions."""
    regions: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in silences:
        if start - cursor >= MIN_SPEECH_REGION_SECONDS:
            regions.append((cursor, start))
        cursor = max(cursor, end)
    if duration - cursor >= MIN_SPEECH_REGION_SECONDS:
        regions.append((cursor, duration))

    padded: list[tuple[float, float]] = []
    for start, end in regions:
        low = max(0.0, start - SEGMENT_PADDING_SECONDS)
        high = min(duration, end + SEGMENT_PADDING_SECONDS)
        if high - low < MIN_SPEECH_REGION_SECONDS:
            continue
        if padded and low - padded[-1][1] < min_silence / 2:
            padded[-1] = (padded[-1][0], high)
        else:
            padded.append((low, high))
    return padded


class FfmpegWhisperEngine:
    """whisper.cpp transcription through ffmpeg's `whisper` audio filter."""

    def availability(self, settings: object) -> str | None:
        if self._ffmpeg_path(settings) is None:
            return ERROR_FFMPEG_MISSING
        model = Path(getattr(settings, "whisper_model_path"))
        if not model.is_file():
            return ERROR_MODEL_MISSING
        return None

    @staticmethod
    def _ffmpeg_path(settings: object) -> str | None:
        configured = str(getattr(settings, "ffmpeg_bin", "ffmpeg"))
        if _looks_like_path(configured):
            return configured if Path(configured).is_file() else None
        return shutil.which(configured)

    async def transcribe(
        self, settings: object, audio_path: Path
    ) -> TranscriptionOutcome:
        availability = self.availability(settings)
        if availability is not None:
            raise TranscriptionFailure(availability)

        ffmpeg = self._ffmpeg_path(settings)
        if ffmpeg is None:  # defensive: availability already checked
            raise TranscriptionFailure(ERROR_FFMPEG_MISSING)

        model = Path(getattr(settings, "whisper_model_path")).resolve()
        workdir = audio_path.parent
        model_relative = os.path.relpath(model, workdir)
        # ffmpeg's filtergraph cannot express a drive colon reliably; refuse a
        # cross-drive model path with an actionable code instead of a cryptic
        # graph parse error.
        if ":" in model_relative:
            raise TranscriptionFailure(
                ERROR_MODEL_PATH_LAYOUT,
                "Set WHISPER_MODEL_PATH on the same drive as the voice-notes directory.",
            )

        timeout = float(getattr(settings, "transcription_timeout_seconds", 300.0))
        deadline = time.monotonic() + timeout
        regions = await self._plan_regions(settings, ffmpeg, audio_path, deadline)
        if not regions:
            regions = [(0.0, None)]

        texts: list[str] = []
        language: str | None = None
        for index, (start, end) in enumerate(regions):
            remaining = deadline - time.monotonic()
            if remaining <= 0.5:
                raise TranscriptionFailure(ERROR_TIMEOUT)
            output_name = _output_name()
            output_path = workdir / output_name
            output_path.unlink(missing_ok=True)
            graph = _build_filter_graph(
                settings,
                model_relative,
                output_name,
                start=start,
                end=end,
            )
            try:
                returncode, stderr = await _run_ffmpeg(
                    ffmpeg,
                    [
                        "-hide_banner",
                        "-nostdin",
                        "-y",
                        "-i",
                        str(audio_path),
                        "-af",
                        graph,
                        "-f",
                        "null",
                        "-",
                    ],
                    workdir,
                    remaining,
                )
                if returncode != 0:
                    logger.warning(
                        "Transcription engine exited with code %s (segment %s/%s).",
                        returncode,
                        index + 1,
                        len(regions),
                    )
                    lowered = stderr.lower()
                    if (
                        "failed to initialize whisper context" in lowered
                        or "failed to open" in lowered
                    ):
                        raise TranscriptionFailure(ERROR_MODEL_MISSING)
                    raise TranscriptionFailure(ERROR_ENGINE_FAILED)
                try:
                    raw = output_path.read_text(encoding="utf-8")
                except OSError as exc:
                    raise TranscriptionFailure(ERROR_INVALID_OUTPUT) from exc
            finally:
                output_path.unlink(missing_ok=True)

            try:
                outcome = _parse_output(raw)
            except TranscriptionFailure as exc:
                if exc.code == ERROR_EMPTY and len(regions) > 1:
                    logger.info(
                        "Empty transcript for speech region %s/%s.", index + 1, len(regions)
                    )
                    continue
                raise
            texts.append(outcome.text)
            if language is None and outcome.language:
                language = outcome.language

        combined = " ".join(text for text in texts if text).strip()
        if not combined:
            raise TranscriptionFailure(ERROR_EMPTY)
        return TranscriptionOutcome(text=combined, language=language)

    async def _plan_regions(
        self,
        settings: object,
        ffmpeg: str,
        audio_path: Path,
        deadline: float,
    ) -> list[tuple[float, float | None]]:
        """Speech regions to decode separately, or [] for a single whole-file pass."""
        min_silence = float(getattr(settings, "whisper_segment_silence_seconds", 0.6) or 0.0)
        if min_silence <= 0:
            return []
        remaining = deadline - time.monotonic()
        if remaining <= 1.0:
            return []

        workdir = audio_path.parent
        duration, peak = await _probe_levels(ffmpeg, audio_path, workdir, remaining)
        if not duration or peak is None or duration < 2.0:
            return []

        threshold = min(
            MAX_SILENCE_THRESHOLD_DB,
            max(MIN_SILENCE_THRESHOLD_DB, peak - SILENCE_RANGE_BELOW_PEAK_DB),
        )
        remaining = deadline - time.monotonic()
        if remaining <= 1.0:
            return []
        silences = await _detect_silences(
            ffmpeg, audio_path, workdir, min_silence, threshold, remaining
        )
        regions = _speech_regions(silences, duration, min_silence)
        if len(regions) < 2:
            return []
        if len(regions) > MAX_SPEECH_REGIONS:
            logger.info(
                "Leaving %s speech regions in one pass (limit %s).",
                len(regions),
                MAX_SPEECH_REGIONS,
            )
            return []
        logger.info(
            "Transcribing %s speech regions separately (%.1fs audio).",
            len(regions),
            duration,
        )
        return [(start, end) for start, end in regions]


def _output_name() -> str:
    return f".transcribe-{uuid.uuid4().hex}.txt"


async def _run_ffmpeg(
    ffmpeg: str,
    args: list[str],
    workdir: Path,
    timeout: float,
) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        ffmpeg,
        *args,
        cwd=str(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=max(1.0, timeout))
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.wait()
        raise TranscriptionFailure(ERROR_TIMEOUT) from exc
    return process.returncode, stderr.decode("utf-8", errors="replace")


async def _probe_levels(
    ffmpeg: str,
    audio_path: Path,
    workdir: Path,
    timeout: float,
) -> tuple[float | None, float | None]:
    returncode, stderr = await _run_ffmpeg(
        ffmpeg,
        [
            "-hide_banner",
            "-nostdin",
            "-i",
            str(audio_path),
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        workdir,
        timeout,
    )
    if returncode != 0:
        return None, None
    return _parse_ffmpeg_duration(stderr), _parse_max_volume(stderr)


async def _detect_silences(
    ffmpeg: str,
    audio_path: Path,
    workdir: Path,
    min_silence: float,
    threshold_db: float,
    timeout: float,
) -> list[tuple[float, float]]:
    returncode, stderr = await _run_ffmpeg(
        ffmpeg,
        [
            "-hide_banner",
            "-nostdin",
            "-i",
            str(audio_path),
            "-af",
            f"silencedetect=noise={threshold_db:.1f}dB:d={min_silence:.2f}",
            "-f",
            "null",
            "-",
        ],
        workdir,
        timeout,
    )
    if returncode != 0:
        return []
    duration = _parse_ffmpeg_duration(stderr)
    if duration is None:
        return []
    return _parse_silences(stderr, duration)


def _build_filter_graph(
    settings: object,
    model_relative: str,
    output_name: str,
    start: float | None = None,
    end: float | None = None,
) -> str:
    """Assemble the ffmpeg `whisper` filtergraph for one audio file or region."""
    language = str(getattr(settings, "whisper_language", "auto") or "auto")
    use_gpu = "1" if bool(getattr(settings, "whisper_use_gpu", False)) else "0"
    queue_seconds = max(1, int(getattr(settings, "whisper_queue_seconds", 20)))
    prefix = ""
    if start is not None or end is not None:
        trim = f"atrim=start={max(0.0, start or 0.0):.3f}"
        if end is not None:
            trim += f":end={end:.3f}"
        # Sample-accurate region trimming; output `-ss/-t` interacts badly with
        # the whisper filter's queue (measured 2026-10-23).
        prefix = f"{trim},asetpts=PTS-STARTPTS,"
    return (
        prefix
        + "whisper=model="
        + _escape_filter_value(model_relative)
        + f":language={_escape_filter_value(language)}"
        + f":queue={queue_seconds}"
        + f":use_gpu={use_gpu}"
        # Plain text: this ffmpeg build's JSON writer does not escape quotes in
        # the transcript (verified 2026-10-23), which makes its JSON invalid for
        # any utterance containing a quotation.
        + ":format=text"
        + f":destination={_escape_filter_value(output_name)}"
    )


def _parse_output(raw: str) -> TranscriptionOutcome:
    """Parse whisper.cpp / ffmpeg whisper output.

    The filter is invoked with `format=text`, so the destination normally holds
    plain transcribed text (one line per chunk). JSON shapes are still accepted
    because other whisper.cpp front ends emit one JSON document (`text`,
    `transcription`, or `segments`) and ffmpeg's whisper filter can emit one
    JSON object per chunk (JSON Lines)::

        {"start":0,"end":11000,"text":"Hello there."}
    """
    stripped = raw.strip()
    if not stripped:
        raise TranscriptionFailure(ERROR_EMPTY)

    if stripped.startswith("{") or stripped.startswith("["):
        documents = _load_json_documents(stripped)
        parts: list[str] = []
        language: str | None = None
        for document in documents:
            if language is None:
                language = _document_language(document)
            text = _extract_text(document)
            if text:
                parts.append(text)
        combined = " ".join(parts).strip()
        if not combined:
            raise TranscriptionFailure(ERROR_EMPTY)
        return TranscriptionOutcome(text=combined, language=language)

    text = " ".join(line.strip() for line in stripped.splitlines() if line.strip())
    if not text:
        raise TranscriptionFailure(ERROR_EMPTY)
    return TranscriptionOutcome(text=text)


def _load_json_documents(raw: str) -> list[dict]:
    """Return the JSON object(s) in `raw`, accepting JSON or JSON Lines."""
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    else:
        if isinstance(payload, dict):
            return [payload]
        if isinstance(payload, list):
            documents = [item for item in payload if isinstance(item, dict)]
            if documents:
                return documents
        raise TranscriptionFailure(ERROR_INVALID_OUTPUT)

    documents: list[dict] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            parsed = json.loads(line)
        except ValueError as exc:
            raise TranscriptionFailure(ERROR_INVALID_OUTPUT) from exc
        if not isinstance(parsed, dict):
            raise TranscriptionFailure(ERROR_INVALID_OUTPUT)
        documents.append(parsed)
    if not documents:
        raise TranscriptionFailure(ERROR_INVALID_OUTPUT)
    return documents


def _document_language(document: dict) -> str | None:
    """Best-effort language code from one output object."""
    candidates = [document.get("language")]
    result = document.get("result")
    if isinstance(result, dict):
        candidates.append(result.get("language"))
    params = document.get("params")
    if isinstance(params, dict):
        candidates.append(params.get("language"))
    for value in candidates:
        if isinstance(value, str) and value.strip() and value.strip().lower() != "unknown":
            return value.strip()
    return None


def _extract_text(payload: dict) -> str:
    direct = payload.get("text")
    if isinstance(direct, str):
        return direct.strip()
    segments = payload.get("transcription") or payload.get("segments")
    if isinstance(segments, list):
        parts = [
            segment["text"].strip()
            for segment in segments
            if isinstance(segment, dict) and isinstance(segment.get("text"), str)
        ]
        return " ".join(part for part in parts if part).strip()
    return ""
