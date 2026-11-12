export type AppView = "chat" | "memories" | "voice";

export interface Conversation {
  id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export type MessageRole = "system" | "user" | "assistant" | "tool";

export interface MemoryEvidenceItem {
  id: string;
  kind: MemoryKind;
  statement: string;
  origin: MemoryOrigin;
  status: MemoryStatus;
  source_conversation_id: string | null;
  source_message_ids: string[];
}

/** Response metadata stored with an assistant message (never prompt input). */
export interface MessageMetadata {
  memory_evidence?: MemoryEvidenceItem[];
}

export interface Message {
  id: string;
  conversation_id: string;
  seq: number;
  role: MessageRole;
  content: string;
  created_at: string;
  model_tag: string | null;
  generation_status: string | null;
  metadata?: MessageMetadata | null;
}

export interface ExportPayload {
  format: string;
  format_version: number;
  exported_at: string;
  scope: { includes: string[]; excludes: string[] };
  counts: {
    conversations: number;
    messages: number;
    memories: number;
    memory_history: number;
  };
  persona: unknown;
  conversations: unknown[];
  messages: unknown[];
  memories: unknown[];
  memory_history: unknown[];
}

export interface HealthStatus {
  status: "ok" | "degraded";
  ollama: {
    reachable: boolean;
    error: string | null;
  };
  model: {
    tag: string;
    available: boolean;
  };
}

export type ApiError = {
  code: string;
  message: string;
};

export type ChatEvent =
  | { type: "user_message"; message: Message }
  | { type: "assistant_started"; message_id: string }
  | { type: "delta"; content: string }
  | { type: "done"; message: Message }
  | { type: "error"; code: string; message: string };

// --------------------------------------------------- memory management API ---
// Mirrors backend/app/models/schemas.py for Phase 2, Step 6 endpoints.

export type MemoryKind = "fact" | "preference" | "goal" | "event" | "person" | "other";
export type MemoryOrigin = "user_stated" | "inferred";
export type MemoryStatus =
  | "accepted"
  | "review"
  | "confirmed"
  | "conflicted"
  | "superseded"
  | "orphaned"
  | "deleted";

export interface MemoryProvenance {
  conversation_id: string | null;
  message_ids: string[];
  valid: boolean;
}

export interface MemorySummary {
  id: string;
  kind: MemoryKind;
  statement: string;
  subject: string | null;
  keywords: string[];
  origin: MemoryOrigin;
  status: MemoryStatus;
  confidence: number;
  provenance: MemoryProvenance;
  created_at: string;
  updated_at: string;
  last_confirmed_at: string | null;
}

/** Content snapshot stored inside a history entry (audit data). */
export interface MemorySnapshot {
  statement?: string;
  subject?: string | null;
  keywords?: string[];
  status?: string;
  origin?: string;
  confidence?: number;
}

export interface MemoryHistoryValue {
  memory?: MemorySnapshot;
  before?: MemorySnapshot;
  after?: MemorySnapshot;
  status_from?: string;
  status_to?: string;
  reason?: string | null;
}

export interface MemoryHistoryEntry {
  id: string;
  memory_id: string;
  event: string;
  value: MemoryHistoryValue;
  created_at: string;
}

export interface MemoryDetail extends MemorySummary {
  history: MemoryHistoryEntry[];
}

export interface MemoryListResponse {
  items: MemorySummary[];
  total: number;
  limit: number;
  offset: number;
}

export interface MemoryFilters {
  status?: MemoryStatus;
  kind?: MemoryKind;
  origin?: MemoryOrigin;
  limit?: number;
  offset?: number;
}

export type VoiceNoteStatus = "pending" | "processing" | "completed" | "failed";
export type TranscriptSource = "machine" | "user";

/** Mirrors backend/app/models/schemas.py (Phase 4). No filesystem paths. */
export interface VoiceNoteSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  audio_available: boolean;
  audio_deleted: boolean;
  audio_duration_seconds: number | null;
  transcript_status: VoiceNoteStatus;
  transcript_source: TranscriptSource | null;
  transcript_preview: string | null;
}

export interface VoiceNoteToChatResult {
  conversation_id: string;
  message_id: string;
}

export interface VoiceNote extends VoiceNoteSummary {
  audio_content_type: string;
  audio_bytes: number;
  audio_deleted_at: string | null;
  transcript: string | null;
  machine_transcript: string | null;
  transcript_error: string | null;
  transcript_language: string | null;
  transcript_updated_at: string | null;
}

/** Only fields the PATCH endpoint accepts; protected fields are rejected. */
export interface MemoryCorrectionInput {
  statement?: string;
  subject?: string | null;
  keywords?: string[];
}

export function formatTimestamp(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
