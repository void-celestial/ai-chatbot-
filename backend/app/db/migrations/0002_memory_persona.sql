-- Phase 2 schema: persona settings, structured memories, and memory history.
-- Base tables (conversations, messages) are created by schema.sql; this file
-- only ADDS tables, indexes, triggers, and an FTS5 index.

CREATE TABLE IF NOT EXISTS persona (
    id TEXT PRIMARY KEY CHECK (id = 'default'),
    name TEXT NOT NULL,
    tone TEXT NOT NULL CHECK (tone IN ('warm', 'calm', 'playful', 'direct')),
    formality TEXT NOT NULL CHECK (formality IN ('casual', 'neutral', 'formal')),
    custom_instructions TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('fact', 'preference', 'goal', 'event', 'person', 'other')),
    statement TEXT NOT NULL,
    subject TEXT,
    keywords_json TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL CHECK (origin IN ('user_stated', 'inferred')),
    status TEXT NOT NULL CHECK (status IN ('accepted', 'review', 'confirmed', 'conflicted', 'superseded', 'orphaned', 'deleted')),
    confidence REAL NOT NULL DEFAULT 0.5,
    source_conversation_id TEXT REFERENCES conversations (id) ON DELETE SET NULL,
    source_message_ids_json TEXT NOT NULL DEFAULT '[]',
    related_ids_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_confirmed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_memories_status ON memories (status);
CREATE INDEX IF NOT EXISTS idx_memories_conversation ON memories (source_conversation_id);

CREATE TABLE IF NOT EXISTS memory_history (
    id TEXT PRIMARY KEY,
    memory_id TEXT NOT NULL REFERENCES memories (id) ON DELETE CASCADE,
    event TEXT NOT NULL CHECK (event IN ('created', 'corrected', 'merged', 'conflicted')),
    value_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_history_memory ON memory_history (memory_id, created_at);

-- @conditional:fts5
-- Everything below is applied only when SQLite's FTS5 module is available.
-- The external-content table is kept in sync with `memories` by triggers, and
-- the index is rebuildable (retrieval can re-run this section's final INSERT).

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    statement,
    keywords_json,
    content='memories',
    content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts (rowid, statement, keywords_json)
    VALUES (new.rowid, new.statement, new.keywords_json);
END;

CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts (memories_fts, rowid, statement, keywords_json)
    VALUES ('delete', old.rowid, old.statement, old.keywords_json);
END;

CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts (memories_fts, rowid, statement, keywords_json)
    VALUES ('delete', old.rowid, old.statement, old.keywords_json);
    INSERT INTO memories_fts (rowid, statement, keywords_json)
    VALUES (new.rowid, new.statement, new.keywords_json);
END;

-- Seed the index (empty on a fresh migration; also serves as a rebuild hook).
INSERT INTO memories_fts (rowid, statement, keywords_json)
    SELECT rowid, statement, keywords_json FROM memories
    WHERE statement IS NOT NULL AND keywords_json IS NOT NULL;
