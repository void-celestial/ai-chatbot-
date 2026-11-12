import logging
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..services.ollama_client import OllamaError

logger = logging.getLogger(__name__)

router = APIRouter()


class ModelStatus(BaseModel):
    tag: str
    available: bool


class OllamaStatus(BaseModel):
    reachable: bool
    error: str | None = None


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"]
    ollama: OllamaStatus
    model: ModelStatus


async def _ollama_state(request: Request) -> HealthOut:
    settings = request.app.state.settings
    ollama = request.app.state.ollama
    ollama_status = OllamaStatus(reachable=False, error=None)
    model_available = False
    try:
        installed = await ollama.list_models()
        ollama_status = OllamaStatus(reachable=True, error=None)
        model_available = settings.model_tag in installed
        if not model_available:
            ollama_status = OllamaStatus(
                reachable=True,
                error=f"Model '{settings.model_tag}' is not installed. Run `ollama list` to compare.",
            )
    except OllamaError as exc:
        logger.warning("Ollama health check failed: %s", exc.message)
        ollama_status = OllamaStatus(reachable=False, error=exc.message)

    overall: Literal["ok", "degraded"] = (
        "ok" if ollama_status.reachable and model_available else "degraded"
    )
    return HealthOut(
        status=overall,
        ollama=ollama_status,
        model=ModelStatus(tag=settings.model_tag, available=model_available),
    )


@router.get("/api/health", response_model=HealthOut)
async def health(request: Request) -> HealthOut:
    """Always HTTP 200; payload describes Ollama connectivity and model state."""
    state: dict[str, Any] = await _ollama_state(request)
    return HealthOut.model_validate(state)
