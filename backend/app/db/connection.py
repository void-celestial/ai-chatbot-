import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Iterator

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def init_db(db_path: Path) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.executescript(_SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()


@contextmanager
def db_connection(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield one configured connection per request; commit on success."""
    conn = sqlite3.connect(Path(db_path), check_same_thread=False, timeout=10)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        yield conn
        conn.commit()
    finally:
        conn.close()
