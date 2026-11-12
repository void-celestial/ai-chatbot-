import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import { VoiceNotesView } from "./VoiceNotesView";
import type { RenderResult } from "../test/harness";
import {
  buttonContaining,
  buttonExact,
  clickAsync,
  flush,
  render,
  setValue,
  textOf,
} from "../test/harness";
import type { VoiceNote, VoiceNoteSummary } from "../types";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    listVoiceNotes: vi.fn(),
    getVoiceNote: vi.fn(),
    uploadVoiceNote: vi.fn(),
    transcribeVoiceNote: vi.fn(),
    updateVoiceNote: vi.fn(),
    deleteVoiceNoteAudio: vi.fn(),
    deleteVoiceNote: vi.fn(),
    sendVoiceNoteToChat: vi.fn(),
  };
});

const NOTE_ID = "a".repeat(32);

function summary(overrides: Partial<VoiceNoteSummary> = {}): VoiceNoteSummary {
  return {
    id: NOTE_ID,
    title: "Morning idea",
    created_at: "2026-10-22T10:00:00.000Z",
    updated_at: "2026-10-22T10:00:00.000Z",
    audio_available: true,
    audio_deleted: false,
    audio_duration_seconds: 12,
    transcript_status: "pending",
    transcript_source: null,
    transcript_preview: null,
    ...overrides,
  };
}

function note(overrides: Partial<VoiceNote> = {}): VoiceNote {
  return {
    ...summary(),
    audio_content_type: "audio/wav",
    audio_bytes: 2048,
    audio_deleted_at: null,
    transcript: null,
    machine_transcript: null,
    transcript_error: null,
    transcript_language: null,
    transcript_updated_at: null,
    ...overrides,
  };
}

let view: RenderResult | null = null;

async function mount(): Promise<RenderResult> {
  view = render(<VoiceNotesView onBackToChat={() => undefined} />);
  await flush();
  return view;
}

beforeEach(() => {
  vi.mocked(client.listVoiceNotes).mockResolvedValue([]);
  vi.mocked(client.uploadVoiceNote).mockResolvedValue(note());
  vi.mocked(client.transcribeVoiceNote).mockResolvedValue(
    note({ transcript_status: "processing" }),
  );
  vi.mocked(client.updateVoiceNote).mockResolvedValue(
    note({ transcript: "Corrected.", transcript_source: "user" }),
  );
  vi.mocked(client.deleteVoiceNoteAudio).mockResolvedValue(undefined);
  vi.mocked(client.deleteVoiceNote).mockResolvedValue(undefined);
  vi.mocked(client.sendVoiceNoteToChat).mockResolvedValue({
    conversation_id: "c".repeat(32),
    message_id: "m".repeat(32),
  });
});

afterEach(() => {
  view?.unmount();
  view = null;
  vi.clearAllMocks();
  document.body.innerHTML = "";
  Reflect.deleteProperty(window, "MediaRecorder");
  Reflect.deleteProperty(window.navigator, "mediaDevices");
});

describe("voice-notes screen", () => {
  it("shows the empty state and reports unsupported browser recording", async () => {
    const rendered = await mount();

    expect(textOf(rendered.container)).toContain("No voice notes yet");
    expect(textOf(rendered.container)).toContain("does not support microphone recording");
  });

  it("imports a local file with its name-derived title", async () => {
    const rendered = await mount();
    const input = rendered.container.querySelector<HTMLInputElement>("#voice-import-input")!;
    const file = new File([new Uint8Array([1, 2, 3])], "sample.wav", { type: "audio/wav" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();

    expect(client.uploadVoiceNote).toHaveBeenCalledWith(file, {
      title: "sample",
      contentType: "audio/wav",
    });
  });

  it("rejects an oversized file before upload", async () => {
    const rendered = await mount();
    const input = rendered.container.querySelector<HTMLInputElement>("#voice-import-input")!;
    const file = new File([new Uint8Array(1)], "huge.wav", { type: "audio/wav" });
    Object.defineProperty(file, "size", { value: 26 * 1024 * 1024 });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    await act(async () => {
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await flush();

    expect(textOf(rendered.container)).toContain("25 MB or smaller");
    expect(client.uploadVoiceNote).not.toHaveBeenCalled();
  });

  it("lists notes and opens playback and transcript detail", async () => {
    vi.mocked(client.listVoiceNotes).mockResolvedValue([
      summary({ transcript_status: "completed", transcript_source: "machine" }),
    ]);
    vi.mocked(client.getVoiceNote).mockResolvedValue(
      note({
        transcript_status: "completed",
        transcript_source: "machine",
        transcript: "This is the transcript.",
        machine_transcript: "This is the transcript.",
      }),
    );
    const rendered = await mount();

    await clickAsync(buttonContaining(rendered.container, "Morning idea"));

    expect(client.getVoiceNote).toHaveBeenCalledWith(NOTE_ID);
    expect(textOf(rendered.container)).toContain("This is the transcript.");
    const audio = rendered.container.querySelector("audio");
    expect(audio).not.toBeNull();
    expect(audio?.getAttribute("src")).toContain(`/api/voice-notes/${NOTE_ID}/audio`);
  });

  it("saves a transcript correction and reports success", async () => {
    vi.mocked(client.listVoiceNotes).mockResolvedValue([
      summary({ transcript_status: "completed", transcript_source: "machine" }),
    ]);
    vi.mocked(client.getVoiceNote).mockResolvedValue(
      note({
        transcript_status: "completed",
        transcript_source: "machine",
        transcript: "Original words.",
      }),
    );
    const rendered = await mount();
    await clickAsync(buttonContaining(rendered.container, "Morning idea"));
    const textarea = rendered.container.querySelector<HTMLTextAreaElement>("#voice-transcript")!;
    setValue(textarea, "Corrected words.");
    await clickAsync(buttonExact(rendered.container, "Save correction"));

    expect(client.updateVoiceNote).toHaveBeenCalledWith(NOTE_ID, {
      transcript: "Corrected words.",
    });
    await flush();
    expect(textOf(rendered.container)).toContain("Correction saved.");
  });

  it("shows a failed transcription with an actionable error and cancels with Escape", async () => {
    vi.mocked(client.listVoiceNotes).mockResolvedValue([
      summary({ transcript_status: "failed" }),
    ]);
    vi.mocked(client.getVoiceNote).mockResolvedValue(
      note({
        transcript_status: "failed",
        transcript_error: "model_missing",
        transcript_source: "user",
        transcript: "My correction.",
      }),
    );
    const rendered = await mount();
    await clickAsync(buttonContaining(rendered.container, "Morning idea"));

    expect(textOf(rendered.container)).toContain("No local transcription model is installed");

    await clickAsync(buttonExact(rendered.container, "Replace correction…"));
    expect(textOf(rendered.container)).toContain("Replace your corrected transcript?");
    await clickAsync(buttonExact(rendered.container, "Cancel"));
    expect(textOf(rendered.container)).not.toContain("Replace your corrected transcript?");

    await clickAsync(buttonExact(rendered.container, "Replace correction…"));
    await clickAsync(buttonExact(rendered.container, "Replace with new transcript"));
    expect(client.transcribeVoiceNote).toHaveBeenCalledWith(NOTE_ID, true);
  });

  it("offers the transcript as an explicit memory source only after confirmation", async () => {
    const openConversation = vi.fn();
    vi.mocked(client.listVoiceNotes).mockResolvedValue([
      summary({ transcript_status: "completed", transcript_source: "user" }),
    ]);
    vi.mocked(client.getVoiceNote).mockResolvedValue(
      note({
        transcript_status: "completed",
        transcript_source: "user",
        transcript: "My sister Mara lives in Bergen.",
      }),
    );
    view = render(
      <VoiceNotesView
        onBackToChat={() => undefined}
        onOpenConversation={openConversation}
      />,
    );
    await flush();
    await clickAsync(buttonContaining(view.container, "Morning idea"));

    await clickAsync(buttonExact(view.container, "Use transcript for memory…"));
    expect(textOf(view.container)).toContain("Use this transcript as a memory source?");
    expect(client.sendVoiceNoteToChat).not.toHaveBeenCalled();

    await clickAsync(buttonExact(view.container, "Use transcript"));

    expect(client.sendVoiceNoteToChat).toHaveBeenCalledWith(NOTE_ID);
    expect(textOf(view.container)).toContain("Transcript saved to a new conversation");
    await clickAsync(buttonExact(view.container, "Open conversation"));
    expect(openConversation).toHaveBeenCalledWith("c".repeat(32));
  });

  it("deletes audio after confirmation and keeps the transcript visible", async () => {
    vi.mocked(client.listVoiceNotes).mockResolvedValue([
      summary({ transcript_status: "completed", transcript_source: "machine" }),
    ]);
    vi.mocked(client.getVoiceNote)
      .mockResolvedValueOnce(
        note({
          transcript_status: "completed",
          transcript_source: "machine",
          transcript: "Kept words.",
          machine_transcript: "Kept words.",
        }),
      )
      .mockResolvedValueOnce(
        note({
          transcript_status: "completed",
          transcript_source: "machine",
          transcript: "Kept words.",
          machine_transcript: "Kept words.",
          audio_available: false,
          audio_deleted: true,
          audio_deleted_at: "2026-10-22T11:00:00.000Z",
        }),
      );
    const rendered = await mount();
    await clickAsync(buttonContaining(rendered.container, "Morning idea"));

    await clickAsync(buttonExact(rendered.container, "Delete audio…"));
    expect(textOf(rendered.container)).toContain("Delete the audio file?");
    await clickAsync(buttonExact(rendered.container, "Delete audio"));

    expect(client.deleteVoiceNoteAudio).toHaveBeenCalledWith(NOTE_ID);
    expect(textOf(rendered.container)).toContain("audio file was deleted");
    expect(textOf(rendered.container)).toContain("Kept words.");
  });

  it("deletes a note after confirmation and returns to the placeholder", async () => {
    vi.mocked(client.listVoiceNotes).mockResolvedValue([summary()]);
    vi.mocked(client.getVoiceNote).mockResolvedValue(note());
    const rendered = await mount();
    await clickAsync(buttonContaining(rendered.container, "Morning idea"));

    await clickAsync(buttonExact(rendered.container, "Delete note…"));
    expect(textOf(rendered.container)).toContain("Delete this voice note?");
    await clickAsync(buttonExact(rendered.container, "Delete voice note"));

    expect(client.deleteVoiceNote).toHaveBeenCalledWith(NOTE_ID);
    expect(textOf(rendered.container)).toContain("Select a voice note");
  });

  it("shows a list error and recovers on retry", async () => {
    vi.mocked(client.listVoiceNotes)
      .mockRejectedValueOnce(new Error("Backend unavailable"))
      .mockResolvedValueOnce([summary()]);
    const rendered = await mount();

    expect(textOf(rendered.container)).toContain("Backend unavailable");
    await clickAsync(buttonExact(rendered.container, "Retry"));
    expect(textOf(rendered.container)).toContain("Morning idea");
  });

  it("explains microphone permission denial without claiming a save", async () => {
    Object.defineProperty(window, "MediaRecorder", {
      configurable: true,
      value: class {
        static isTypeSupported() {
          return true;
        }
      },
    });
    Object.defineProperty(window.navigator, "mediaDevices", {
      configurable: true,
      value: {
        getUserMedia: vi.fn().mockRejectedValue(
          new DOMException("denied", "NotAllowedError"),
        ),
      },
    });
    const rendered = await mount();

    await clickAsync(buttonExact(rendered.container, "Start recording"));

    expect(textOf(rendered.container)).toContain("Microphone access was denied");
    expect(client.uploadVoiceNote).not.toHaveBeenCalled();
  });
});
