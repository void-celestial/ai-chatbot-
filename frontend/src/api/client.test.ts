import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiRequestError,
  confirmMemory,
  correctMemory,
  deleteConversation,
  deleteMemory,
  deleteVoiceNote,
  deleteVoiceNoteAudio,
  exportData,
  getMemory,
  getVoiceNote,
  listMemories,
  listVoiceNotes,
  rejectMemory,
  sendVoiceNoteToChat,
  supersedeMemory,
  transcribeVoiceNote,
  updateVoiceNote,
  uploadVoiceNote,
  voiceNoteAudioUrl,
} from "./client";
import type { MemoryListResponse, MemorySummary, VoiceNote } from "../types";

const MEMORY_ID = "a".repeat(32);

const memory: MemorySummary = {
  id: MEMORY_ID,
  kind: "fact",
  statement: "The user lives in Oslo.",
  subject: "home",
  keywords: ["oslo"],
  origin: "user_stated",
  status: "accepted",
  confidence: 1,
  provenance: { conversation_id: "c".repeat(32), message_ids: [], valid: true },
  created_at: "2026-10-21T10:00:00.000Z",
  updated_at: "2026-10-21T10:00:00.000Z",
  last_confirmed_at: null,
};

const voiceNote: VoiceNote = {
  id: MEMORY_ID,
  title: "Morning idea",
  created_at: "2026-10-22T10:00:00.000Z",
  updated_at: "2026-10-22T10:00:00.000Z",
  audio_available: true,
  audio_deleted: false,
  audio_duration_seconds: 12,
  transcript_status: "pending",
  transcript_source: null,
  transcript_preview: null,
  audio_content_type: "audio/wav",
  audio_bytes: 2048,
  audio_deleted_at: null,
  transcript: null,
  machine_transcript: null,
  transcript_error: null,
  transcript_language: null,
  transcript_updated_at: null,
};

const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function firstUrl(): string {
  const call = fetchMock.mock.calls[0];
  if (!call) throw new Error("fetch was not called");
  return String(call[0]);
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listMemories", () => {
  it("sends supported filters and pagination", async () => {
    const payload: MemoryListResponse = { items: [memory], total: 1, limit: 20, offset: 40 };
    fetchMock.mockResolvedValue(jsonResponse(payload));

    await expect(
      listMemories({ status: "review", kind: "fact", origin: "inferred", limit: 20, offset: 40 }),
    ).resolves.toEqual(payload);

    const url = firstUrl();
    expect(url).toContain("/api/memories?");
    expect(url).toContain("status=review");
    expect(url).toContain("kind=fact");
    expect(url).toContain("origin=inferred");
    expect(url).toContain("limit=20");
    expect(url).toContain("offset=40");
  });

  it("omits unset filters", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [], total: 0, limit: 20, offset: 0 }));

    await listMemories({ limit: 20, offset: 0 });

    const url = firstUrl();
    expect(url).toContain("limit=20");
    expect(url).not.toContain("status=");
    expect(url).not.toContain("kind=");
    expect(url).not.toContain("origin=");
  });
});

describe("single-memory operations", () => {
  it("reads one memory", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...memory, history: [] }));

    const detail = await getMemory(MEMORY_ID);

    expect(detail.id).toBe(MEMORY_ID);
    expect(firstUrl()).toContain(`/api/memories/${MEMORY_ID}`);
  });

  it("confirms with POST and no body", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...memory, status: "confirmed" }));

    const updated = await confirmMemory(MEMORY_ID);

    expect(updated.status).toBe("confirmed");
    const call = fetchMock.mock.calls[0];
    expect(call?.[1]?.method).toBe("POST");
    expect(firstUrl()).toContain(`/api/memories/${MEMORY_ID}/confirm`);
  });

  it("rejects with an optional reason body", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...memory, status: "deleted" }));

    await rejectMemory(MEMORY_ID, "Not accurate");

    const call = fetchMock.mock.calls[0];
    expect(call?.[1]?.method).toBe("POST");
    expect(call?.[1]?.body).toBe(JSON.stringify({ reason: "Not accurate" }));
    expect(firstUrl()).toContain(`/api/memories/${MEMORY_ID}/reject`);
  });

  it("rejects with no body when no reason is given", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...memory, status: "deleted" }));

    await rejectMemory(MEMORY_ID);

    const call = fetchMock.mock.calls[0];
    expect(call?.[1]?.body).toBeUndefined();
  });

  it("supersedes with an optional reason body", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...memory, status: "superseded" }));

    await supersedeMemory(MEMORY_ID, "Moved away");

    const call = fetchMock.mock.calls[0];
    expect(call?.[1]?.method).toBe("POST");
    expect(call?.[1]?.body).toBe(JSON.stringify({ reason: "Moved away" }));
    expect(firstUrl()).toContain(`/api/memories/${MEMORY_ID}/supersede`);
  });

  it("supersedes with no body when no reason is given", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...memory, status: "superseded" }));

    await supersedeMemory(MEMORY_ID);

    expect(fetchMock.mock.calls[0]?.[1]?.body).toBeUndefined();
  });

  it("patches only the supplied correction fields", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...memory, statement: "The user lives in Bergen." }));

    await correctMemory(MEMORY_ID, { statement: "The user lives in Bergen." });

    const call = fetchMock.mock.calls[0];
    expect(call?.[1]?.method).toBe("PATCH");
    expect(call?.[1]?.body).toBe(JSON.stringify({ statement: "The user lives in Bergen." }));
  });

  it("accepts the empty 204 response from delete", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await expect(deleteMemory(MEMORY_ID)).resolves.toBeUndefined();
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("DELETE");
  });
});

describe("conversation deletion", () => {
  it("deletes with DELETE and accepts the empty 204 response", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await expect(deleteConversation("conversation-1")).resolves.toBeUndefined();

    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("DELETE");
    expect(firstUrl()).toContain("/api/conversations/conversation-1");
  });

  it("surfaces a deletion failure", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Could not delete the conversation." }, 500));

    await expect(deleteConversation("conversation-1")).rejects.toMatchObject({
      code: "http_500",
      message: "Could not delete the conversation.",
    });
  });
});

describe("data export", () => {
  it("fetches the JSON export", async () => {
    const payload = {
      format: "companion-export",
      format_version: 1,
      exported_at: "2026-10-22T12:00:00.000Z",
      scope: { includes: [], excludes: [] },
      counts: { conversations: 0, messages: 0, memories: 0, memory_history: 0 },
      persona: null,
      conversations: [],
      messages: [],
      memories: [],
      memory_history: [],
    };
    fetchMock.mockResolvedValue(jsonResponse(payload));

    await expect(exportData()).resolves.toEqual(payload);
    expect(firstUrl()).toContain("/api/export");
  });

  it("surfaces an export failure", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Could not export local data." }, 500));

    await expect(exportData()).rejects.toMatchObject({
      code: "http_500",
      message: "Could not export local data.",
    });
  });
});

describe("voice notes", () => {
  it("lists and loads voice notes", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse([voiceNote]))
      .mockResolvedValueOnce(jsonResponse(voiceNote));

    await expect(listVoiceNotes()).resolves.toEqual([voiceNote]);
    await expect(getVoiceNote(MEMORY_ID)).resolves.toEqual(voiceNote);

    expect(String(fetchMock.mock.calls[1]?.[0])).toContain(
      `/api/voice-notes/${MEMORY_ID}`,
    );
  });

  it("uploads the blob as a raw body with a title and content type", async () => {
    fetchMock.mockResolvedValue(jsonResponse(voiceNote, 201));
    const audio = new Blob(["data"], { type: "audio/webm" });

    await uploadVoiceNote(audio, { title: "Idea", contentType: "audio/webm" });

    const call = fetchMock.mock.calls[0];
    if (!call) throw new Error("fetch was not called");
    const headers = call[1]?.headers as Record<string, string>;
    expect(call[1]?.method).toBe("POST");
    expect(call[1]?.body).toBe(audio);
    expect(headers["Content-Type"]).toBe("audio/webm");
    expect(firstUrl()).toContain("title=Idea");
  });

  it("starts transcription with a force flag", async () => {
    fetchMock.mockResolvedValue(jsonResponse(voiceNote, 202));

    await transcribeVoiceNote(MEMORY_ID, true);

    const call = fetchMock.mock.calls[0];
    expect(call?.[1]?.method).toBe("POST");
    expect(call?.[1]?.body).toBe(JSON.stringify({ force: true }));
    expect(firstUrl()).toContain(`/api/voice-notes/${MEMORY_ID}/transcribe`);
  });

  it("patches the corrected transcript", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ...voiceNote, transcript: "Edited." }));

    await updateVoiceNote(MEMORY_ID, { transcript: "Edited." });

    const call = fetchMock.mock.calls[0];
    expect(call?.[1]?.method).toBe("PATCH");
    expect(call?.[1]?.body).toBe(JSON.stringify({ transcript: "Edited." }));
  });

  it("deletes audio and notes with DELETE", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await expect(deleteVoiceNoteAudio(MEMORY_ID)).resolves.toBeUndefined();
    await expect(deleteVoiceNote(MEMORY_ID)).resolves.toBeUndefined();

    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("DELETE");
    expect(String(fetchMock.mock.calls[0]?.[0])).toContain(
      `/api/voice-notes/${MEMORY_ID}/audio`,
    );
    expect(String(fetchMock.mock.calls[1]?.[0])).toContain(`/api/voice-notes/${MEMORY_ID}`);
  });

  it("sends a transcript to chat with POST and no body", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ conversation_id: "c".repeat(32), message_id: "m".repeat(32) }, 201),
    );

    await expect(sendVoiceNoteToChat(MEMORY_ID)).resolves.toMatchObject({
      conversation_id: "c".repeat(32),
    });

    const call = fetchMock.mock.calls[0];
    expect(call?.[1]?.method).toBe("POST");
    expect(call?.[1]?.body).toBeUndefined();
    expect(firstUrl()).toContain(`/api/voice-notes/${MEMORY_ID}/send-to-chat`);
  });

  it("builds the audio URL and surfaces a 503 transcription error", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: "The local transcription model is missing." }, 503),
    );

    expect(voiceNoteAudioUrl(MEMORY_ID)).toContain(`/api/voice-notes/${MEMORY_ID}/audio`);
    await expect(transcribeVoiceNote(MEMORY_ID)).rejects.toMatchObject({
      code: "http_503",
      message: "The local transcription model is missing.",
    });
  });
});

describe("errors", () => {
  it("turns a validation array into one readable message", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { detail: [{ msg: "Statement cannot be empty." }, { msg: "Too long." }] },
        422,
      ),
    );

    await expect(correctMemory(MEMORY_ID, { statement: "" })).rejects.toMatchObject({
      name: "ApiRequestError",
      code: "http_422",
      message: "Statement cannot be empty. Too long.",
    });
  });

  it("surfaces backend detail text and status codes", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Memory not found." }, 404));

    const failure = await getMemory(MEMORY_ID).catch((error: unknown) => error);
    expect(failure).toBeInstanceOf(ApiRequestError);
    expect(failure).toMatchObject({ code: "http_404", message: "Memory not found." });
  });

  it("fails delete on non-2xx", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "Could not delete the memory." }, 500));

    await expect(deleteMemory(MEMORY_ID)).rejects.toMatchObject({ code: "http_500" });
  });
});
