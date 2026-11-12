import type {
  ApiError,
  ChatEvent,
  Conversation,
  ExportPayload,
  HealthStatus,
  MemoryCorrectionInput,
  MemoryDetail,
  MemoryFilters,
  MemoryListResponse,
  MemorySummary,
  Message,
  VoiceNote,
  VoiceNoteSummary,
  VoiceNoteToChatResult,
} from "../types";

/**
 * Where the API lives. Endpoint paths below are unchanged; only the origin is
 * decided here.
 *
 * - Production / desktop: the backend serves this bundle from its own origin,
 *   so requests use relative paths ("") and follow the host and port the
 *   window was loaded from. The port is never hardcoded, so a changed
 *   APP_PORT cannot strand the UI on a dead address.
 * - Development (`vite dev`, tests): the page is served by Vite, so fall back
 *   to the local backend's default address.
 *
 * `VITE_API_BASE_URL` still wins in either mode, and a trailing slash is
 * trimmed so paths never double up.
 */
const configuredApiBase: string = (
  (import.meta.env && import.meta.env.VITE_API_BASE_URL) || ""
)
  .trim()
  .replace(/\/+$/, "");

const API_BASE: string =
  configuredApiBase || (import.meta.env && import.meta.env.DEV ? "http://127.0.0.1:8000" : "");

/** What to tell the user when the local backend is not answering at all. */
const BACKEND_UNREACHABLE_MESSAGE =
  "The assistant backend is not running. Restart the application.";

/** A stream that ends before its terminal event is an interruption, not a reply. */
const STREAM_INTERRUPTED_MESSAGE =
  "The reply was interrupted before it finished. Press Try again to retry.";

export class ApiRequestError extends Error implements ApiError {
  code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "ApiRequestError";
    this.code = code;
  }
}

/**
 * `fetch` with one honest failure mode: a refused or dropped connection (which
 * arrives as a bare `TypeError: Failed to fetch`) becomes an actionable error
 * instead of leaking a browser message. A user-initiated abort is re-thrown
 * untouched so stopping generation is never reported as a failure.
 */
async function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(input, init);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiRequestError("backend_unreachable", BACKEND_UNREACHABLE_MESSAGE);
  }
}

/** Turn FastAPI's `detail` (string or validation array) into one message. */
function describeDetail(detail: unknown): string {
  if (typeof detail === "string") return detail.trim();
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (item && typeof item === "object" && "msg" in item && typeof item.msg === "string") {
          return item.msg;
        }
        return "";
      })
      .filter((message) => message.length > 0);
    if (messages.length > 0) return messages.join(" ");
  }
  return "";
}

async function toApiError(response: Response): Promise<ApiRequestError> {
  let detail = "";
  try {
    const body: unknown = await response.json();
    const detailValue =
      body && typeof body === "object" && "detail" in body
        ? (body as { detail?: unknown }).detail
        : undefined;
    detail = describeDetail(detailValue);
  } catch {
    detail = "";
  }
  if (!detail) detail = `${response.status} ${response.statusText}`;
  return new ApiRequestError(`http_${response.status}`, detail);
}

export async function fetchHealth(): Promise<HealthStatus> {
  const response = await apiFetch(`${API_BASE}/api/health`);
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as HealthStatus;
}

export async function listConversations(): Promise<Conversation[]> {
  const response = await apiFetch(`${API_BASE}/api/conversations`);
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as Conversation[];
}

export async function createConversation(title?: string): Promise<Conversation> {
  const response = await apiFetch(`${API_BASE}/api/conversations`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(title ? { title } : {}),
  });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as Conversation;
}

export async function listMessages(conversationId: string): Promise<Message[]> {
  const response = await apiFetch(`${API_BASE}/api/conversations/${conversationId}/messages`);
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as Message[];
}

/** Permanently delete one conversation and its messages (204, no body). */
export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/conversations/${encodeURIComponent(conversationId)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw await toApiError(response);
}

// ------------------------------------------------------------ data export ---

/** Read-only JSON export of the documented local scope. */
export async function exportData(): Promise<ExportPayload> {
  const response = await apiFetch(`${API_BASE}/api/export`);
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as ExportPayload;
}

// ------------------------------------------------------- memory management ---

export async function listMemories(filters: MemoryFilters = {}): Promise<MemoryListResponse> {
  const params = new URLSearchParams();
  if (filters.status) params.set("status", filters.status);
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.origin) params.set("origin", filters.origin);
  if (filters.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters.offset !== undefined) params.set("offset", String(filters.offset));
  const query = params.toString();
  const response = await apiFetch(`${API_BASE}/api/memories${query ? `?${query}` : ""}`);
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as MemoryListResponse;
}

export async function getMemory(memoryId: string): Promise<MemoryDetail> {
  const response = await apiFetch(`${API_BASE}/api/memories/${encodeURIComponent(memoryId)}`);
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as MemoryDetail;
}

export async function confirmMemory(memoryId: string): Promise<MemorySummary> {
  const response = await apiFetch(
    `${API_BASE}/api/memories/${encodeURIComponent(memoryId)}/confirm`,
    { method: "POST" },
  );
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as MemorySummary;
}

export async function rejectMemory(memoryId: string, reason?: string): Promise<MemorySummary> {
  const response = await apiFetch(
    `${API_BASE}/api/memories/${encodeURIComponent(memoryId)}/reject`,
    {
      method: "POST",
      headers: reason ? { "Content-Type": "application/json" } : undefined,
      body: reason ? JSON.stringify({ reason }) : undefined,
    },
  );
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as MemorySummary;
}

export async function supersedeMemory(
  memoryId: string,
  reason?: string,
): Promise<MemorySummary> {
  const response = await apiFetch(
    `${API_BASE}/api/memories/${encodeURIComponent(memoryId)}/supersede`,
    {
      method: "POST",
      headers: reason ? { "Content-Type": "application/json" } : undefined,
      body: reason ? JSON.stringify({ reason }) : undefined,
    },
  );
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as MemorySummary;
}

export async function correctMemory(
  memoryId: string,
  changes: MemoryCorrectionInput,
): Promise<MemorySummary> {
  const response = await apiFetch(`${API_BASE}/api/memories/${encodeURIComponent(memoryId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as MemorySummary;
}

/** Soft-delete: 204 on success (idempotent). No body is returned. */
export async function deleteMemory(memoryId: string): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/memories/${encodeURIComponent(memoryId)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw await toApiError(response);
}

// ------------------------------------------------------------ voice notes ---

/** List voice-note summaries, newest first. */
export async function listVoiceNotes(): Promise<VoiceNoteSummary[]> {
  const response = await apiFetch(`${API_BASE}/api/voice-notes`);
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as VoiceNoteSummary[];
}

export async function getVoiceNote(noteId: string): Promise<VoiceNote> {
  const response = await apiFetch(`${API_BASE}/api/voice-notes/${encodeURIComponent(noteId)}`);
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as VoiceNote;
}

/**
 * Upload a recording or local audio file as a raw body. The backend derives the
 * extension from `Content-Type`; the file name never becomes a path.
 */
export async function uploadVoiceNote(
  audio: Blob,
  options: { title?: string; contentType?: string } = {},
): Promise<VoiceNote> {
  const params = options.title ? `?title=${encodeURIComponent(options.title)}` : "";
  const response = await apiFetch(`${API_BASE}/api/voice-notes${params}`, {
    method: "POST",
    headers: { "Content-Type": options.contentType || audio.type || "application/octet-stream" },
    body: audio,
  });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as VoiceNote;
}

export async function transcribeVoiceNote(noteId: string, force = false): Promise<VoiceNote> {
  const response = await apiFetch(
    `${API_BASE}/api/voice-notes/${encodeURIComponent(noteId)}/transcribe`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force }),
    },
  );
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as VoiceNote;
}

export async function updateVoiceNote(
  noteId: string,
  changes: { title?: string; transcript?: string },
): Promise<VoiceNote> {
  const response = await apiFetch(`${API_BASE}/api/voice-notes/${encodeURIComponent(noteId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(changes),
  });
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as VoiceNote;
}

export async function deleteVoiceNoteAudio(noteId: string): Promise<void> {
  const response = await apiFetch(
    `${API_BASE}/api/voice-notes/${encodeURIComponent(noteId)}/audio`,
    { method: "DELETE" },
  );
  if (!response.ok) throw await toApiError(response);
}

export async function deleteVoiceNote(noteId: string): Promise<void> {
  const response = await apiFetch(`${API_BASE}/api/voice-notes/${encodeURIComponent(noteId)}`, {
    method: "DELETE",
  });
  if (!response.ok) throw await toApiError(response);
}

/** Explicitly use the transcript as a memory source (new conversation + extraction). */
export async function sendVoiceNoteToChat(
  noteId: string,
): Promise<VoiceNoteToChatResult> {
  const response = await apiFetch(
    `${API_BASE}/api/voice-notes/${encodeURIComponent(noteId)}/send-to-chat`,
    { method: "POST" },
  );
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as VoiceNoteToChatResult;
}

/** Stable URL for the audio element; the backend validates the id. */
export function voiceNoteAudioUrl(noteId: string): string {
  return `${API_BASE}/api/voice-notes/${encodeURIComponent(noteId)}/audio`;
}

/** POST to an SSE chat endpoint and invoke `onEvent` for each parsed event. */
export async function streamChat(
  path: string,
  body: unknown,
  onEvent: (event: ChatEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await apiFetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    throw await toApiError(response);
  }
  if (!response.body) {
    throw new ApiRequestError("no_stream", "The backend did not return a response stream.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  // A reply is only complete once the server says so (`done`, or an `error`
  // event it produced). Anything else — a dropped connection, or a body that
  // closes early — is an interruption, which the caller can retry.
  let terminated = false;

  const dispatch = (rawEvent: string) => {
    for (const line of rawEvent.split("\n")) {
      const trimmed = line.trim();
      if (!trimmed.startsWith("data: ")) continue;
      try {
        const event = JSON.parse(trimmed.slice("data: ".length)) as ChatEvent;
        if (event.type === "done" || event.type === "error") terminated = true;
        onEvent(event);
      } catch {
        // Ignore malformed events; the stream is the source of truth.
      }
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let separatorIndex: number;
      while ((separatorIndex = buffer.indexOf("\n\n")) !== -1) {
        const rawEvent = buffer.slice(0, separatorIndex);
        buffer = buffer.slice(separatorIndex + 2);
        if (rawEvent.trim()) dispatch(rawEvent);
      }
    }
    if (buffer.trim()) dispatch(buffer);
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiRequestError("stream_interrupted", STREAM_INTERRUPTED_MESSAGE);
  } finally {
    reader.releaseLock();
  }

  if (!terminated) {
    throw new ApiRequestError("stream_interrupted", STREAM_INTERRUPTED_MESSAGE);
  }
}
