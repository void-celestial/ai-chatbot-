"""Ollama HTTP client.

Errors are mapped to typed exceptions so the chat layer can surface clear,
user-actionable diagnostics instead of raw stack traces:

- `OllamaUnavailable` — Ollama is not running or not reachable at its base URL
- `ModelNotFound`     — Ollama is running but the configured model tag is missing
- `GenerationFailed`  — Ollama responded with an error, timed out, or returned
                        malformed data during generation
"""

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from ..config import Settings

_CONNECT_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout)

# How long a healthy "configured model is installed" preflight answer may be
# reused. Every chat message used to pay a fresh /api/tags round trip
# (measured ~30ms of server-side manifest scanning plus a connection). Only a
# *positive* preflight is ever cached: if the configured model is missing the
# answer is re-fetched every time, so a newly pulled model is picked up
# immediately, and the streaming call raises ModelNotFound on its own anyway.
MODELS_CACHE_SECONDS = 30.0


class OllamaError(Exception):
    code = "ollama_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class OllamaUnavailable(OllamaError):
    code = "ollama_unavailable"


class ModelNotFound(OllamaError):
    code = "model_not_found"


class GenerationFailed(OllamaError):
    code = "generation_failed"


class OllamaClient:
    """Thin async wrapper around the local Ollama HTTP API."""

    def __init__(self, settings: Settings) -> None:
        self._base = settings.ollama_base_url.rstrip("/")
        self._connect_timeout = settings.connect_timeout_seconds
        self._request_timeout = settings.request_timeout_seconds
        self._health_timeout = settings.health_timeout_seconds
        self._model_tag = settings.model_tag
        # Lifecycle knobs (see config.py). Kept as plain values so every
        # generation request carries the same keep-alive and thinking policy.
        self._keep_alive = getattr(settings, "ollama_keep_alive", "") or None
        self._think = getattr(settings, "ollama_think", None)
        self._max_output_tokens = getattr(settings, "max_output_tokens", 0) or 0
        # Positive-preflight cache: (monotonic timestamp, installed tags).
        self._models_cache: tuple[float, list[str]] | None = None
        self._models_cache_ttl = MODELS_CACHE_SECONDS

    def build_payload(
        self,
        model: str,
        messages: list[dict[str, str]],
        num_ctx: int,
        *,
        stream: bool,
        json_format: bool = False,
    ) -> dict[str, Any]:
        """One place that decides every generation option sent to Ollama.

        `keep_alive` keeps the model resident between messages, `think` is only
        sent when explicitly configured (so other models keep their own
        default), and `num_predict` is only sent when a positive cap is set.
        """
        options: dict[str, Any] = {"num_ctx": num_ctx}
        if self._max_output_tokens > 0:
            options["num_predict"] = self._max_output_tokens
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "options": options,
        }
        if json_format:
            payload["format"] = "json"
        if self._keep_alive:
            payload["keep_alive"] = self._keep_alive
        if self._think is not None:
            payload["think"] = self._think
        return payload

    def _timeout(self, total: float | None) -> httpx.Timeout:
        return httpx.Timeout(total, connect=self._connect_timeout)

    async def list_models(self) -> list[str]:
        """Return locally installed model tags, or raise a typed OllamaError.

        A healthy preflight (configured model present) is reused for up to
        `MODELS_CACHE_SECONDS` so chat messages skip the round trip; any
        failure or missing-model answer bypasses the cache entirely.
        """
        cached = self._models_cache
        if cached is not None and time.monotonic() - cached[0] < self._models_cache_ttl:
            return list(cached[1])
        names = await self._fetch_models()
        if self._model_tag in names:
            self._models_cache = (time.monotonic(), names)
        return names

    async def _fetch_models(self) -> list[str]:
        """One /api/tags round trip (uncached); raises typed OllamaError."""
        try:
            async with httpx.AsyncClient(timeout=self._timeout(self._health_timeout)) as client:
                response = await client.get(f"{self._base}/api/tags")
        except _CONNECT_ERRORS as exc:
            raise OllamaUnavailable(
                f"Cannot reach Ollama at {self._base}. Is `ollama serve` running?"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaUnavailable(f"Ollama request failed: {exc.__class__.__name__}") from exc

        if response.status_code != 200:
            raise OllamaUnavailable(f"Ollama /api/tags returned HTTP {response.status_code}")

        try:
            data = response.json()
        except ValueError as exc:
            raise OllamaUnavailable("Ollama returned a malformed /api/tags response.") from exc

        models = data.get("models") or []
        names = []
        for entry in models:
            name = entry.get("name") or entry.get("model")
            if isinstance(name, str) and name:
                names.append(name)
        return sorted(set(names))

    async def chat_stream(
        self, model: str, messages: list[dict[str, str]], num_ctx: int
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream /api/chat chunks as dicts; raises typed errors on failure."""
        payload = self.build_payload(model, messages, num_ctx, stream=True)
        try:
            async with httpx.AsyncClient(timeout=self._timeout(None)) as client:
                async with client.stream("POST", f"{self._base}/api/chat", json=payload) as response:
                    if response.status_code == 404:
                        body = (await response.aread()).decode("utf-8", errors="replace")
                        detail = _ErrorMessage(body)
                        raise ModelNotFound(
                            f"Model '{model}' was not found in Ollama. "
                            f"Details: {detail.text or f'HTTP 404'}"
                        )
                    if response.status_code != 200:
                        body = (await response.aread()).decode("utf-8", errors="replace")
                        detail = _ErrorMessage(body)
                        raise GenerationFailed(
                            f"Ollama returned HTTP {response.status_code}"
                            + (f": {detail.text}" if detail.text else "")
                        )
                    async for line in response.aiter_lines():
                        stripped = line.strip()
                        if not stripped:
                            continue
                        try:
                            chunk = json.loads(stripped)
                        except ValueError:
                            continue  # tolerate a malformed line; the stream is source of truth
                        if isinstance(chunk, dict):
                            yield chunk
        except ModelNotFound:
            raise
        except GenerationFailed:
            raise
        except _CONNECT_ERRORS as exc:
            raise OllamaUnavailable(
                f"Connection to Ollama at {self._base} was lost: {exc.__class__.__name__}"
            ) from exc
        except httpx.ReadTimeout as exc:
            raise GenerationFailed(
                "Ollama stopped responding while generating (read timeout)."
            ) from exc
        except httpx.HTTPError as exc:
            raise GenerationFailed(f"Ollama stream failed: {exc.__class__.__name__}") from exc


    async def chat_json(
        self,
        model: str,
        messages: list[dict[str, str]],
        num_ctx: int,
        timeout_seconds: float | None = None,
    ) -> str:
        """Single-shot, non-streaming /api/chat call that forces JSON output.

        Used by background steps (e.g. memory extraction) that need a complete
        structured response rather than a stream. Reuses the same typed errors
        as `chat_stream`; the returned value is the raw model content string,
        which every caller must treat as untrusted.
        """
        payload = self.build_payload(
            model, messages, num_ctx, stream=False, json_format=True
        )
        # The body is read through the streaming transport on purpose:
        # background extraction must be cancellable when a new foreground
        # message arrives (see extraction_scheduler). Cancelling a task
        # blocked in `aread()` closes this connection, which aborts the
        # generation server-side — measured 2026-10-25: the next chat request
        # got its first token 0.24s after the cancel. A plain
        # `client.post()` cancellation left the connection open and Ollama
        # kept generating: the next request still waited 37s behind it.
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout(timeout_seconds if timeout_seconds is not None else self._request_timeout)
            ) as client:
                async with client.stream(
                    "POST", f"{self._base}/api/chat", json=payload
                ) as response:
                    status_code = response.status_code
                    body_bytes = await response.aread()
        except _CONNECT_ERRORS as exc:
            raise OllamaUnavailable(
                f"Cannot reach Ollama at {self._base}. Is `ollama serve` running?"
            ) from exc
        except httpx.ReadTimeout as exc:
            raise GenerationFailed("Ollama stopped responding (request timeout).") from exc
        except httpx.HTTPError as exc:
            raise GenerationFailed(
                f"Ollama request failed: {exc.__class__.__name__}"
            ) from exc

        body_text = body_bytes.decode("utf-8", errors="replace")
        if status_code == 404:
            detail = _ErrorMessage(body_text)
            raise ModelNotFound(
                f"Model '{model}' was not found in Ollama. "
                f"Details: {detail.text or 'HTTP 404'}"
            )
        if status_code != 200:
            detail = _ErrorMessage(body_text)
            raise GenerationFailed(
                f"Ollama returned HTTP {status_code}"
                + (f": {detail.text}" if detail.text else "")
            )

        try:
            data = json.loads(body_text)
        except ValueError as exc:
            raise GenerationFailed("Ollama returned a malformed chat response.") from exc

        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str):
            raise GenerationFailed("Ollama chat response contained no content.")
        return content


class _ErrorMessage:
    """Best-effort extraction of an error message from an Ollama error body."""

    def __init__(self, body: str) -> None:
        self.text = ""
        try:
            data = json.loads(body)
        except ValueError:
            self.text = body.strip()[:300]
            return
        if isinstance(data, dict):
            error = data.get("error")
            if isinstance(error, str):
                self.text = error[:300]
