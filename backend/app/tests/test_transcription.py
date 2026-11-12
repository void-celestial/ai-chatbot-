"""Phase 4: local transcription engine contract (no model or subprocess needed)."""

import shutil

import pytest

from backend.app.config import Settings
from backend.app.services.transcription import (
    ERROR_EMPTY,
    ERROR_FFMPEG_MISSING,
    ERROR_INVALID_OUTPUT,
    ERROR_MODEL_MISSING,
    FfmpegWhisperEngine,
    TranscriptionFailure,
    _build_filter_graph,
    _escape_filter_value,
    _parse_output,
)


def test_parse_output_joins_whisper_cpp_segments():
    outcome = _parse_output(
        """
        {"result": {"language": "en"},
         "transcription": [
           {"text": " Hello there,"},
           {"text": " this is local."}
         ]}
        """
    )
    assert outcome.text == "Hello there, this is local."
    assert outcome.language == "en"


def test_parse_output_accepts_direct_text_and_params_language():
    outcome = _parse_output('{"text": "  Direct text.  ", "params": {"language": "de"}}')
    assert outcome.text == "Direct text."
    assert outcome.language == "de"


def test_parse_output_reads_plain_text_and_joins_lines():
    outcome = _parse_output(
        'All nouns alongside the word "safe" for you always begin with a capital letter.\n'
        "\n"
        "Even in the middle of a sentence.\n"
    )
    assert outcome.text == (
        'All nouns alongside the word "safe" for you always begin with a capital '
        "letter. Even in the middle of a sentence."
    )
    assert outcome.language is None


def test_parse_output_treats_empty_plain_text_as_no_speech():
    with pytest.raises(TranscriptionFailure) as failure:
        _parse_output("  \n\n ")
    assert failure.value.code == ERROR_EMPTY


def test_parse_output_reads_ffmpeg_json_lines():
    """ffmpeg 9's whisper filter writes one JSON object per chunk (JSONL)."""
    outcome = _parse_output(
        '{"start":0,"end":11000,"text":" And so, my fellow Americans,"}\n'
        '{"start":0,"end":11000,"text":"ask not what your country can do for you."}\n'
    )
    assert outcome.text == (
        "And so, my fellow Americans, ask not what your country can do for you."
    )
    assert outcome.language is None


def test_parse_output_reads_json_line_language_and_ignores_unknown():
    outcome = _parse_output('{"language":"hi","text":"\u0928\u092e\u0938\u094d\u0924\u0947"}\n')
    assert outcome.text == "\u0928\u092e\u0938\u094d\u0924\u0947"
    assert outcome.language == "hi"

    unknown = _parse_output('{"language":"unknown","text":"Something."}\n')
    assert unknown.language is None


def test_parse_output_rejects_a_broken_json_line():
    with pytest.raises(TranscriptionFailure) as failure:
        _parse_output('{"text":"ok"}\nnot json\n')
    assert failure.value.code == ERROR_INVALID_OUTPUT


def test_parse_output_rejects_a_json_array_of_non_objects():
    with pytest.raises(TranscriptionFailure) as failure:
        _parse_output("[1, 2]")
    assert failure.value.code == ERROR_INVALID_OUTPUT


def test_filter_graph_carries_queue_language_and_json_output():
    settings = Settings(
        whisper_language="hi", whisper_queue_seconds=15, whisper_use_gpu=False
    )
    graph = _build_filter_graph(settings, "../models/ggml-medium.bin", "out.json")
    assert "model=../models/ggml-medium.bin" in graph
    assert "language=hi" in graph
    assert "queue=15" in graph
    assert "use_gpu=0" in graph
    assert "format=text" in graph
    assert "destination=out.json" in graph


def test_default_whisper_queue_avoids_the_three_second_filter_default():
    assert Settings().whisper_queue_seconds == 20


def test_parse_output_rejects_empty_and_malformed_payloads():
    with pytest.raises(TranscriptionFailure) as empty:
        _parse_output('{"transcription": []}')
    assert empty.value.code == ERROR_EMPTY
    # Plain text is the normal `format=text` output, so malformed JSON means a
    # payload that still claims to be JSON.
    with pytest.raises(TranscriptionFailure) as malformed:
        _parse_output('{"text": "unterminated}')
    assert malformed.value.code == ERROR_INVALID_OUTPUT


def test_filter_values_escape_spaces_and_special_characters():
    assert _escape_filter_value("my model.bin") == "my\\ model.bin"
    assert _escape_filter_value("a:b,c") == "a\\:b\\,c"
    assert _escape_filter_value("dir\\file") == "dir/file"


def test_availability_reports_missing_ffmpeg(tmp_path):
    settings = Settings(
        ffmpeg_bin="definitely-not-a-real-ffmpeg-binary",
        whisper_model_path=tmp_path / "model.bin",
    )
    assert FfmpegWhisperEngine().availability(settings) == ERROR_FFMPEG_MISSING


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_availability_reports_missing_model(tmp_path):
    settings = Settings(
        ffmpeg_bin="ffmpeg",
        whisper_model_path=tmp_path / "missing-model.bin",
    )
    assert FfmpegWhisperEngine().availability(settings) == ERROR_MODEL_MISSING


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_transcribe_stops_before_running_when_model_is_missing(tmp_path):
    settings = Settings(
        ffmpeg_bin="ffmpeg",
        whisper_model_path=tmp_path / "missing-model.bin",
    )
    with pytest.raises(TranscriptionFailure) as failure:
        import asyncio

        asyncio.run(FfmpegWhisperEngine().transcribe(settings, tmp_path / "note.wav"))
    assert failure.value.code == ERROR_MODEL_MISSING
