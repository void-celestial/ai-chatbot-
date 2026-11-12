-- Phase 4 schema: human-recorded voice notes and their transcripts.
-- Audio bytes stay on disk under the configured voice-notes directory; this
-- table stores only generated file metadata, lifecycle state, and transcript
-- text. No message rows or memories are created automatically.

CREATE TABLE IF NOT EXISTS voice_notes (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    audio_filename TEXT NOT NULL,
    audio_content_type TEXT NOT NULL,
    audio_bytes INTEGER NOT NULL CHECK (audio_bytes > 0),
    audio_duration_seconds REAL,
    audio_deleted_at TEXT,
    machine_transcript TEXT,
    transcript TEXT,
    transcript_source TEXT CHECK (transcript_source IN ('machine', 'user')),
    transcript_status TEXT NOT NULL DEFAULT 'pending'
        CHECK (transcript_status IN ('pending', 'processing', 'completed', 'failed')),
    transcript_error TEXT,
    transcript_language TEXT,
    transcript_updated_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_voice_notes_audio_filename ON voice_notes (audio_filename);
CREATE INDEX IF NOT EXISTS idx_voice_notes_created ON voice_notes (created_at DESC, id ASC);
CREATE INDEX IF NOT EXISTS idx_voice_notes_status ON voice_notes (transcript_status);
