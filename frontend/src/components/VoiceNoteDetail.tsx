import { useState } from "react";
import { voiceNoteAudioUrl } from "../api/client";
import { formatTimestamp } from "../types";
import type { VoiceNote, VoiceNoteToChatResult } from "../types";

interface Props {
  note: VoiceNote;
  isTranscribing: boolean;
  isSaving: boolean;
  isSending: boolean;
  isDeleting: boolean;
  onTranscribe: (force: boolean) => Promise<boolean>;
  onSaveTranscript: (transcript: string) => Promise<boolean>;
  onSendToChat: () => Promise<VoiceNoteToChatResult | null>;
  onOpenConversation?: (conversationId: string) => void;
  onDeleteAudio: () => Promise<boolean>;
  onDeleteNote: () => Promise<boolean>;
}

const TRANSCRIPTION_ERRORS: Record<string, string> = {
  ffmpeg_missing: "The local ffmpeg binary was not found.",
  model_missing:
    "No local transcription model is installed, or it could not be loaded. Install a whisper.cpp model and set WHISPER_MODEL_PATH.",
  model_path_not_relocatable:
    "The configured model path cannot be reached from the voice-notes folder. Put the model on the same drive.",
  timeout: "Transcription timed out. The audio is unchanged; try again.",
  engine_failed: "The local transcription engine failed.",
  empty_transcript: "No speech was detected in the recording.",
  invalid_output: "The engine returned an unreadable transcript.",
  audio_missing: "The audio file is missing.",
  interrupted_by_restart: "Transcription was interrupted by an app restart. Try again.",
};

function errorText(code: string | null): string | null {
  if (!code) return null;
  return TRANSCRIPTION_ERRORS[code] ?? `Transcription failed (${code}).`;
}

function formatDuration(seconds: number | null): string | null {
  if (!seconds || seconds <= 0) return null;
  const total = Math.round(seconds);
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function TranscriptEditor({
  note,
  isSaving,
  onSave,
}: {
  note: VoiceNote;
  isSaving: boolean;
  onSave: (transcript: string) => Promise<boolean>;
}) {
  const [draft, setDraft] = useState(note.transcript ?? "");
  const trimmed = draft.trim();
  const unchanged = trimmed === (note.transcript ?? "").trim();
  const canSave = trimmed.length > 0 && !unchanged && !isSaving;

  const save = async () => {
    if (!canSave) return;
    await onSave(trimmed);
  };

  return (
    <div className="voice-transcript-editor">
      <label htmlFor="voice-transcript">Transcript</label>
      <textarea
        id="voice-transcript"
        value={draft}
        rows={8}
        maxLength={50000}
        disabled={isSaving}
        onChange={(event) => setDraft(event.target.value)}
        placeholder="No transcript yet. You can type one manually."
      />
      <div className="voice-editor-actions">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => void save()}
          disabled={!canSave}
        >
          {isSaving ? "Saving…" : "Save correction"}
        </button>
        {!trimmed && <span className="voice-note">A transcript cannot be empty.</span>}
      </div>
      {note.transcript_source === "user" && (
        <p className="voice-hint">
          Your correction is stored separately from the last machine transcript.
        </p>
      )}
      {note.transcript_source === "user" &&
        note.machine_transcript &&
        note.machine_transcript !== note.transcript && (
          <details className="voice-machine-transcript">
            <summary>Show last machine transcript</summary>
            <p>{note.machine_transcript}</p>
          </details>
        )}
    </div>
  );
}

export function VoiceNoteDetail({
  note,
  isTranscribing,
  isSaving,
  isSending,
  isDeleting,
  onTranscribe,
  onSaveTranscript,
  onSendToChat,
  onOpenConversation,
  onDeleteAudio,
  onDeleteNote,
}: Props) {
  const [mode, setMode] = useState<
    "view" | "confirmForce" | "confirmAudio" | "confirmNote" | "confirmSend"
  >("view");
  const [saved, setSaved] = useState(false);
  const [sentConversationId, setSentConversationId] = useState<string | null>(null);
  const processing = note.transcript_status === "processing";
  const error = errorText(note.transcript_error);
  const duration = formatDuration(note.audio_duration_seconds);
  const transcribeLabel =
    note.transcript_source === "user"
      ? "Replace correction…"
      : note.transcript_status === "failed"
        ? "Retry transcription"
        : note.transcript_status === "completed"
          ? "Transcribe again"
          : "Transcribe locally";

  const startTranscription = async (force: boolean) => {
    const ok = await onTranscribe(force);
    if (ok) setMode("view");
  };

  return (
    <article className="voice-detail" aria-labelledby="voice-detail-heading">
      <header className="voice-detail-header">
        <div>
          <h3 id="voice-detail-heading">{note.title}</h3>
          <p className="voice-meta">
            {formatTimestamp(note.created_at)}
            {duration ? ` · ${duration}` : ""} · {formatBytes(note.audio_bytes)}
          </p>
        </div>
        <span className={`voice-status voice-status-${note.transcript_status}`}>
          {note.transcript_status === "processing"
            ? "Transcribing…"
            : note.transcript_status === "pending"
              ? "Not transcribed"
              : note.transcript_status === "failed"
                ? "Failed"
                : note.transcript_source === "user"
                  ? "Corrected"
                  : "Transcribed"}
        </span>
      </header>

      <section className="voice-audio" aria-label="Audio playback">
        {note.audio_available ? (
          <audio controls preload="none" src={voiceNoteAudioUrl(note.id)}>
            Your browser cannot play this audio.
          </audio>
        ) : (
          <p className="voice-note" role="status">
            {note.audio_deleted
              ? "The audio file was deleted. The transcript and metadata are kept."
              : "The audio file is missing. The transcript and metadata are kept."}
          </p>
        )}
      </section>

      <section className="voice-transcript" aria-labelledby="voice-transcript-heading">
        <h4 id="voice-transcript-heading">Transcript</h4>
        {processing ? (
          <p className="voice-note" role="status">
            Transcribing locally… This can take a while on CPU. You can leave this
            screen; the result is saved to this note.
          </p>
        ) : null}
        {error && (
          <p className="voice-error" role="alert">
            {error}
          </p>
        )}
        {note.transcript_status === "completed" && note.transcript_source === "machine" && (
          <p className="voice-hint">Machine transcript. Correct it below if needed.</p>
        )}
        <TranscriptEditor
          key={`${note.id}:${note.transcript ?? ""}`}
          note={note}
          isSaving={isSaving}
          onSave={async (transcript) => {
            setSaved(false);
            const ok = await onSaveTranscript(transcript);
            if (ok) setSaved(true);
            return ok;
          }}
        />
        {saved && (
          <p className="voice-saved" role="status">
            Correction saved.
          </p>
        )}
      </section>

      <div className="voice-actions">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => {
            if (note.transcript_source === "user") setMode("confirmForce");
            else void startTranscription(false);
          }}
          disabled={processing || isTranscribing || !note.audio_available}
        >
          {isTranscribing ? "Starting…" : transcribeLabel}
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => setMode("confirmSend")}
          disabled={!note.transcript?.trim() || processing || isSending}
        >
          Use transcript for memory…
        </button>
        {!note.audio_deleted && (
          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setMode("confirmAudio")}
            disabled={isDeleting}
          >
            Delete audio…
          </button>
        )}
        <button
          type="button"
          className="btn btn-danger"
          onClick={() => setMode("confirmNote")}
          disabled={isDeleting}
        >
          Delete note…
        </button>
      </div>

      {mode === "confirmForce" && (
        <section
          className="voice-confirm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="voice-force-heading"
          onKeyDown={(event) => {
            if (event.key === "Escape") setMode("view");
          }}
        >
          <h4 id="voice-force-heading">Replace your corrected transcript?</h4>
          <p>
            A new machine transcript will replace your correction. The audio is unchanged,
            and the last machine transcript stays available for comparison until the new
            one succeeds.
          </p>
          <div className="voice-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void startTranscription(true)}
              disabled={isTranscribing}
            >
              {isTranscribing ? "Starting…" : "Replace with new transcript"}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setMode("view")}>
              Cancel
            </button>
          </div>
        </section>
      )}

      {sentConversationId && (
        <p className="voice-saved" role="status">
          Transcript saved to a new conversation. Normal memory rules apply.{" "}
          {onOpenConversation && (
            <button
              type="button"
              className="btn btn-ghost voice-inline-button"
              onClick={() => onOpenConversation(sentConversationId)}
            >
              Open conversation
            </button>
          )}
        </p>
      )}

      {mode === "confirmSend" && (
        <section
          className="voice-confirm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="voice-send-heading"
          onKeyDown={(event) => {
            if (event.key === "Escape") setMode("view");
          }}
        >
          <h4 id="voice-send-heading">Use this transcript as a memory source?</h4>
          <p>
            A new conversation is created with the current transcript as your message.
            No assistant reply is generated. The existing extraction rules decide what,
            if anything, becomes a memory. Nothing is sent automatically.
          </p>
          <div className="voice-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => {
                void onSendToChat().then((result) => {
                  if (result) {
                    setSentConversationId(result.conversation_id);
                    setMode("view");
                  }
                });
              }}
              disabled={isSending}
            >
              {isSending ? "Saving…" : "Use transcript"}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setMode("view")}>
              Cancel
            </button>
          </div>
        </section>
      )}

      {mode === "confirmAudio" && (
        <section
          className="voice-confirm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="voice-delete-audio-heading"
          onKeyDown={(event) => {
            if (event.key === "Escape") setMode("view");
          }}
        >
          <h4 id="voice-delete-audio-heading">Delete the audio file?</h4>
          <p>
            The recording is removed from local storage. The transcript and note metadata
            stay, so you can still read the words. This cannot be undone from the app, and
            backups or copies outside the app may still contain the audio.
          </p>
          <div className="voice-actions">
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => {
                void onDeleteAudio().then((ok) => {
                  if (ok) setMode("view");
                });
              }}
              disabled={isDeleting}
            >
              {isDeleting ? "Deleting…" : "Delete audio"}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setMode("view")}>
              Cancel
            </button>
          </div>
        </section>
      )}

      {mode === "confirmNote" && (
        <section
          className="voice-confirm"
          role="dialog"
          aria-modal="true"
          aria-labelledby="voice-delete-note-heading"
          onKeyDown={(event) => {
            if (event.key === "Escape") setMode("view");
          }}
        >
          <h4 id="voice-delete-note-heading">Delete this voice note?</h4>
          <p>
            The audio file, transcript, and note record are removed. Backups or previously
            exported copies may still contain a copy, so this is not complete erasure.
          </p>
          <div className="voice-actions">
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => void onDeleteNote()}
              disabled={isDeleting}
            >
              {isDeleting ? "Deleting…" : "Delete voice note"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setMode("view")}
              autoFocus
            >
              Cancel
            </button>
          </div>
        </section>
      )}
    </article>
  );
}
