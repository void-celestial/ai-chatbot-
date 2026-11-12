"""Versioned SQLite migrations.

Design:
- The Phase 1 base schema (`schema.sql`, all IF NOT EXISTS) is applied first by
  `init_db`; versioned migrations live in `migrations/*.sql` as
  `<version>_<name>.sql` and are applied in ascending order exactly once.
- Every applied version is recorded in `schema_migrations`. Running `migrate()`
  again is a no-op once all versions are applied.
- Before the first structural change to an existing database, a backup copy is
  written to `<db parent>/backups/<stem>-pre-v{version}.db`. Existing backups
  are never overwritten; note that backups are persistent copies made BEFORE
  later deletions, so they may retain data deleted afterwards (documented
  retention policy: user-managed, deletable at any time).
- Sections introduced by the marker `-- @conditional:fts5` are applied only if
  this SQLite build provides FTS5; the migration still records its version so
  a build without FTS5 behaves identically (retrieval falls back to LIKE).
- Each migration runs inside a single transaction: either fully applied or
  rolled back, never partially.
"""

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from .utils import utc_now

_MIGRATIONS_DIR = Path(__file__).with_name("migrations")
_FTS_MARKER = "-- @conditional:fts5"


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


def _load_migrations() -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        head, _, rest = path.name.partition("_")
        migrations.append(Migration(version=int(head), name=rest, sql=path.read_text(encoding="utf-8")))
    return migrations


def fts5_available(conn: sqlite3.Connection) -> bool:
    """Probe whether this SQLite build includes the FTS5 module."""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS temp._fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE temp._fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


def _iter_statements(sql: str) -> list[str]:
    """Split a SQL script into executable statements.

    Uses `sqlite3.complete_statement` so trigger bodies (which contain
    semicolons and newlines) stay intact as single statements.
    """
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer.strip() if not line.strip() else buffer):
            statements.append(buffer)
            buffer = ""
    if buffer.strip():
        raise ValueError("Unterminated SQL statement in migration script")
    return statements


def _current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def _latest_version() -> int:
    return max((m.version for m in _load_migrations()), default=0)


def _backup(db_path: Path, version: int) -> Path:
    """Snapshot via SQLite's backup API so WAL-buffered committed data is
    included (a raw file copy can miss `*.db-wal` contents)."""
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{db_path.stem}-pre-v{version}.db"
    if destination.exists():
        return destination  # never overwrite an existing backup
    source = sqlite3.connect(db_path)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()
    return destination


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
               version INTEGER PRIMARY KEY,
               name TEXT NOT NULL,
               applied_at TEXT NOT NULL
           )"""
    )


def _seed_persona(conn: sqlite3.Connection) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO persona (id, name, tone, formality, custom_instructions, created_at, updated_at)
           VALUES ('default', 'Companion', 'warm', 'casual', NULL, ?, ?)""",
        (utc_now(), utc_now()),
    )


def _apply(conn: sqlite3.Connection, migration: Migration) -> None:
    parts = migration.sql.split(_FTS_MARKER)
    statements = _iter_statements(parts[0])
    if len(parts) > 1 and fts5_available(conn):
        statements += _iter_statements(_FTS_MARKER.join(parts[1:]))
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in statements:
            conn.execute(statement)
        conn.execute(
            "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
            (migration.version, migration.name, utc_now()),
        )
        _seed_persona(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def migrate(db_path: Path, backup: bool = True) -> int:
    """Bring the database to the latest migration version; return that version."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        _ensure_migrations_table(conn)
        conn.commit()
        applied = _current_version(conn)
        pending = [m for m in _load_migrations() if m.version > applied]
        if not pending:
            return _current_version(conn)
        if backup and path.exists() and path.stat().st_size > 0:
            _backup(path, min(m.version for m in pending))
        for migration in pending:
            _apply(conn, migration)
            applied = _current_version(conn)
        return applied
    finally:
        conn.close()


def latest_version() -> int:
    return _latest_version()
