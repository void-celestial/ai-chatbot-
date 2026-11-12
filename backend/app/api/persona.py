import logging

from fastapi import APIRouter, HTTPException, Request

from ..db.connection import db_connection
from ..models.schemas import PersonaOut, PersonaUpdateIn
from ..services.persona_service import (
    PersonaPersistenceError,
    get_persona,
    update_persona,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/persona", response_model=PersonaOut)
async def read_persona(request: Request) -> PersonaOut:
    """Return the current persona settings (defaults created on demand)."""
    settings = request.app.state.settings
    with db_connection(settings.db_path) as conn:
        try:
            persona = get_persona(conn)
        except PersonaPersistenceError as exc:
            logger.warning("Persona read failed: %s", exc)
            raise HTTPException(
                status_code=500, detail="Could not read persona settings."
            ) from exc
    return persona


@router.patch("/api/persona", response_model=PersonaOut)
async def update_persona_endpoint(payload: PersonaUpdateIn, request: Request) -> PersonaOut:
    """Apply a partial persona update: only supplied fields change.

    Omitted fields keep their current values. `custom_instructions: null`
    explicitly clears the instructions; null for name/tone/formality is a
    validation error (422). Validation errors are rejected before persisting."""
    settings = request.app.state.settings
    with db_connection(settings.db_path) as conn:
        try:
            persona = update_persona(conn, payload)
        except PersonaPersistenceError as exc:
            logger.warning("Persona update failed: %s", exc)
            raise HTTPException(
                status_code=500, detail="Could not save persona settings."
            ) from exc
    return persona
