"""Persona persistence and customization service.

The companion's configurable persona (name, tone, formality, optional
user-provided style instructions) lives in the single-row `persona` table
created by migration 0002. It is deliberately kept separate from personal
memories: it configures *how* the companion speaks, never *what* it knows.

The row is identified by the fixed id `'default'` (enforced by a database
CHECK) and is created on demand so a database migrated before the seed
ran — or one whose row was lost — still yields usable defaults.
"""

import sqlite3

from ..db.utils import utc_now
from ..models.schemas import PersonaOut, PersonaUpdateIn

DEFAULT_PERSONA_ROW_ID = "default"

# Seed values recorded by migration 0002 in `_seed_persona`. The service uses
# them so a missing persona row can be recreated with the same defaults.
DEFAULT_PERSONA_NAME = "Companion"
DEFAULT_PERSONA_TONE = "warm"
DEFAULT_PERSONA_FORMALITY = "casual"


class PersonaPersistenceError(Exception):
    """Raised when the persona cannot be read or saved (non-validation)."""


def get_persona(conn: sqlite3.Connection) -> PersonaOut:
    """Load the current persona; create the default row if it is missing."""
    try:
        row = conn.execute(
            "SELECT * FROM persona WHERE id = ?", (DEFAULT_PERSONA_ROW_ID,)
        ).fetchone()
        if row is None:
            # Row missing (e.g. deleted before migration seeding): write the
            # defaults now so the read returns real stored values.
            update_persona(conn, PersonaUpdateIn())
            row = conn.execute(
                "SELECT * FROM persona WHERE id = ?", (DEFAULT_PERSONA_ROW_ID,)
            ).fetchone()
    except sqlite3.Error as exc:
        raise PersonaPersistenceError(
            "Could not read the companion persona."
        ) from exc
    return PersonaOut.model_validate(dict(row))


def update_persona(conn: sqlite3.Connection, payload: PersonaUpdateIn) -> PersonaOut:
    """Apply a partial persona update (PATCH semantics).

    Only the fields explicitly present in the request body are applied
    (`payload.model_fields_set` distinguishes omitted from supplied).
    Omitted fields keep their stored values; when the row does not exist
    yet, omitted fields fall back to the migration-seeded defaults.
    """
    try:
        row = conn.execute(
            "SELECT * FROM persona WHERE id = ?", (DEFAULT_PERSONA_ROW_ID,)
        ).fetchone()
        values = {
            "name": DEFAULT_PERSONA_NAME,
            "tone": DEFAULT_PERSONA_TONE,
            "formality": DEFAULT_PERSONA_FORMALITY,
            "custom_instructions": None,
        }
        if row is not None:
            values.update(
                name=row["name"],
                tone=row["tone"],
                formality=row["formality"],
                custom_instructions=row["custom_instructions"],
            )
        for field in payload.model_fields_set:
            values[field] = getattr(payload, field)

        if row is None:
            created_at = utc_now()
            conn.execute(
                """INSERT INTO persona (id, name, tone, formality, custom_instructions, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    DEFAULT_PERSONA_ROW_ID,
                    values["name"],
                    values["tone"],
                    values["formality"],
                    values["custom_instructions"],
                    created_at,
                    created_at,
                ),
            )
        else:
            conn.execute(
                """UPDATE persona
                   SET name = ?, tone = ?, formality = ?, custom_instructions = ?, updated_at = ?
                   WHERE id = ?""",
                (
                    values["name"],
                    values["tone"],
                    values["formality"],
                    values["custom_instructions"],
                    utc_now(),
                    DEFAULT_PERSONA_ROW_ID,
                ),
            )
        row = conn.execute(
            "SELECT * FROM persona WHERE id = ?", (DEFAULT_PERSONA_ROW_ID,)
        ).fetchone()
    except sqlite3.Error as exc:
        raise PersonaPersistenceError(
            "Could not save the companion persona."
        ) from exc
    return PersonaOut.model_validate(dict(row))
