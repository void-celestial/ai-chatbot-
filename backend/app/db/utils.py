"""Shared helpers for timestamps and IDs.

Timestamps are UTC with millisecond precision in ISO-8601 with a "Z" suffix,
which sorts correctly as text and parses directly in JavaScript via `Date`.
"""

from datetime import datetime, timezone
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_id() -> str:
    return uuid4().hex
