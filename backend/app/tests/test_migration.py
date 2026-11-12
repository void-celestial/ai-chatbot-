import sqlite3
from contextlib import closing

import pytest

from backend.app.config import Settings
from backend.app.db.connection import init_db
from backend.app.db.migration import _backup, fts5_available, latest_version, migrate
from backend.app.main import create_app
from backend.app.tests.conftest import TEST_MODEL_TAG, FakeOllama

from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "db" / "schema.sql"


@pytest.fixture
def phase1_db(tmp_path: Path) -> Path:
    """A database created exactly the way Phase 1 created real databases."""
    db_path = tmp_path / "companion.db"
    init_db(db_path)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            "INSERT INTO conversations (id, title, status, created_at, updated_at) VALUES (?, ?, 'active', ?, ?)",
            ("conv1", "Phase 1 chat", "2026-10-20T15:58:31.998Z", "2026-10-20T15:58:31.998Z"),
        )
        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, created_at, model_tag, generation_status)
               VALUES ('msg1', 'conv1', 'user', 'Hello from Phase 1', '2026-10-20T15:58:32.000Z', NULL, NULL)""",
        )
        conn.execute(
            """INSERT INTO messages (id, conversation_id, role, content, created_at, model_tag, generation_status)
               VALUES ('msg2', 'conv1', 'assistant', 'Hi! It is good to see you.', '2026-10-20T15:59:00.000Z', 'qwen3.5:9b', 'complete')""",
        )
        conn.commit()
    return db_path


def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def test_migration_preserves_phase1_data_and_adds_tables(phase1_db):
    version = migrate(phase1_db)
    assert version == latest_version()

    with _connect(phase1_db) as conn:
        conversations = conn.execute("SELECT id, title FROM conversations").fetchall()
        messages = conn.execute(
            "SELECT id, role, content, generation_status FROM messages ORDER BY seq"
        ).fetchall()
        assert [(c["id"], c["title"]) for c in conversations] == [("conv1", "Phase 1 chat")]
        assert [m["content"] for m in messages] == ["Hello from Phase 1", "Hi! It is good to see you."]
        assert messages[1]["generation_status"] == "complete"

        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"persona", "memories", "memory_history", "schema_migrations"} <= tables
        assert fts5_available(conn)

        applied = conn.execute("SELECT version, name FROM schema_migrations ORDER BY version").fetchall()
        # The Phase 1 base schema is the version-0 baseline; only versioned
        # migration files appear in schema_migrations, in order, ending at the
        # latest version.
        versions = [r["version"] for r in applied]
        assert versions == sorted(versions)
        assert versions[-1] == latest_version()
        assert all(version > 0 for version in versions)
        assert {"persona", "voice_notes"} <= tables

        persona = conn.execute("SELECT * FROM persona WHERE id = 'default'").fetchone()
        assert persona is not None
        assert persona["name"] == "Companion"
        assert persona["tone"] == "warm"
        assert persona["formality"] == "casual"


def test_migration_is_idempotent_and_backs_up_once(phase1_db):
    first = migrate(phase1_db)
    backups = list((phase1_db.parent / "backups").glob("*.db"))
    assert len(backups) == 1

    # Backup content matches the database before the structural change.
    with _connect(backups[0]) as bak, _connect(phase1_db) as conn:
        assert bak.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
        assert "memories" not in {
            r["name"] for r in bak.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    second = migrate(phase1_db)  # repeated run must be a no-op
    third = migrate(phase1_db)
    assert first == second == third
    assert len(list((phase1_db.parent / "backups").glob("*.db"))) == 1  # no new copies

    with _connect(phase1_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM persona").fetchone()[0] == 1


def test_backup_is_complete_snapshot_even_with_pending_wal(phase1_db):
    """Committed WAL data must be captured in the pre-migration backup.

    The source connection is held open across the backup call: once a SQLite
    database's last connection closes, the WAL is checkpointed automatically,
    which would make this test vacuous.
    """
    conn = closing(sqlite3.connect(phase1_db)).__enter__()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "INSERT INTO messages (id, conversation_id, role, content, created_at)"
            " VALUES ('msg3', 'conv1', 'user', 'WAL-committed but not yet checkpointed', 'now')"
        )
        conn.commit()

        wal_file = Path(str(phase1_db) + "-wal")
        assert wal_file.exists(), "test setup must actually defer checkpointing"

        destination = _backup(phase1_db, 2)  # runs with the open reader present
    finally:
        conn.close()

    with _connect(destination) as bak:
        # A byte-copy backup misses WAL content; the SQLite backup API must not.
        assert bak.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 3
        assert bak.execute(
            "SELECT content FROM messages WHERE id='msg3'"
        ).fetchone()["content"].startswith("WAL-committed")


def test_fts_sync_triggers(phase1_db):
    migrate(phase1_db)
    with closing(sqlite3.connect(phase1_db)) as conn:
        conn.execute(
            """INSERT INTO memories (id, kind, statement, keywords_json, origin, status, created_at, updated_at)
               VALUES ('mem1', 'fact', 'Mara''s birthday is April 12', '["mara","friend"]', 'user_stated', 'confirmed', 'now', 'now')""",
        )
        hits = conn.execute(
            "SELECT rowid FROM memories_fts WHERE memories_fts MATCH 'mara'"
        ).fetchall()
        assert len(hits) == 1

        # Updating only the statement: 'april' (statement term) must disappear
        # from the index while the untouched keyword 'friend' remains.
        conn.execute("UPDATE memories SET statement = 'Mara''s birthday is May 3' WHERE id = 'mem1'")
        assert conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH 'april'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH 'may'").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH 'friend'").fetchone()[0] == 1

        # Updating keywords re-syncs those too ('friend' is not in the statement).
        conn.execute("UPDATE memories SET keywords_json = '[\"mara\"]' WHERE id = 'mem1'")
        assert conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH 'friend'").fetchone()[0] == 0

        conn.execute("DELETE FROM memories WHERE id = 'mem1'")
        assert conn.execute("SELECT COUNT(*) FROM memories_fts WHERE memories_fts MATCH 'mara'").fetchone()[0] == 0


def test_migration_without_fts5_still_succeeds(phase1_db, monkeypatch):
    monkeypatch.setattr(
        "backend.app.db.migration.fts5_available", lambda conn: False
    )
    version = migrate(phase1_db)
    assert version == latest_version()
    with _connect(phase1_db) as conn:
        tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "memories_fts" not in tables
        assert "memories" in tables
        assert conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2


def test_backup_keeps_existing_backup_untouched(phase1_db):
    destination = _backup(phase1_db, 2)
    content_first = destination.read_bytes()
    destination_again = _backup(phase1_db, 2)
    assert destination_again.read_bytes() == content_first
    assert destination_again == destination


def test_create_app_runs_migration(tmp_path: Path):
    settings_db = tmp_path / "app.db"
    app = create_app(settings=Settings(db_path=settings_db, model_tag=TEST_MODEL_TAG), ollama=FakeOllama())
    with _connect(settings_db) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == latest_version()
        assert conn.execute("SELECT COUNT(*) FROM persona").fetchone()[0] == 1
