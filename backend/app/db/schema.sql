CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- `seq` is a monotonic insertion counter so message order is stable even when
-- two messages share the same UTC timestamp.
CREATE TABLE IF NOT EXISTS messages (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    model_tag TEXT,
    generation_status TEXT CHECK (generation_status IN ('complete', 'interrupted', 'failed')),
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON messages (conversation_id, seq);

CREATE INDEX IF NOT EXISTS idx_conversations_updated
    ON conversations (status, updated_at DESC);
