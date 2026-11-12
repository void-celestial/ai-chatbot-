import { useRef, useState } from "react";
import { useVoiceNotes } from "../hooks/useVoiceNotes";
import { formatTimestamp } from "../types";
import type { VoiceNoteSummary } from "../types";
import { VoiceNoteDetail } from "./VoiceNoteDetail";
import { VoiceRecorder } from "./VoiceRecorder";

interface Props {
  onBackToChat: () => void;
  onOpenConversation?: (conversationId: string) => void;
}

const EXTENSION_TYPES: Record<string, string> = {
  webm: "audio/webm",
  ogg: "audio/ogg",
  oga: "audio/ogg",
  opus: "audio/opus",
  wav: "audio/wav",
  mp3: "audio/mpeg",
  m4a: "audio/mp4",
  mp4: "audio/mp4",
  aac: "audio/aac",
  flac: "audio/flac",
};

function contentTypeFor(file: File): string {
  if (file.type) return file.type;
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  return EXTENSION_TYPES[extension] ?? "application/octet-stream";
}

function titleFor(file: File): string {
  return file.name.replace(/\.[^.]+$/, "").slice(0, 200) || "Imported voice note";
}

function statusLabel(note: VoiceNoteSummary): string {
  if (note.transcript_status === "processing") return "Transcribing…";
  if (note.transcript_status === "failed") return "Failed";
  if (note.transcript_status === "pending") return "Not transcribed";
  return note.transcript_source === "user" ? "Corrected" : "Transcribed";
}

export function VoiceNotesView({ onBackToChat, onOpenConversation }: Props) {
  const {
    notes,
    selectedId,
    detail,
    isLoadingList,
    isLoadingDetail,
    listError,
    detailError,
    actionError,
    isUploading,
    isTranscribing,
    isSaving,
    isSending,
    isDeleting,
    refresh,
    select,
    upload,
    transcribe,
    saveTranscript,
    sendToChat,
    removeAudio,
    removeNote,
    dismissActionError,
  } = useVoiceNotes();
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [importError, setImportError] = useState<string | null>(null);

  const saveRecording = async (blob: Blob, title?: string) => {
    setImportError(null);
    await upload(blob, { title, contentType: blob.type || "audio/webm" });
  };

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    if (!file.size) {
      setImportError("That file is empty.");
      return;
    }
    if (file.size > 25 * 1024 * 1024) {
      setImportError("Audio files must be 25 MB or smaller.");
      return;
    }
    setImportError(null);
    await upload(file, { title: titleFor(file), contentType: contentTypeFor(file) });
    if (fileInputRef.current) fileInputRef.current.value = "";
  };

  return (
    <main className="voice-pane">
      <header className="voice-header">
        <div>
          <h2 className="voice-title">Voice notes</h2>
          <p className="voice-subtitle">
            Record or import audio, transcribe it locally, and correct the words. Nothing
            is sent to a cloud service.
          </p>
        </div>
        <div className="voice-header-actions">
          <button type="button" className="btn btn-secondary" onClick={() => void refresh()}>
            Refresh
          </button>
          <button type="button" className="btn btn-ghost" onClick={onBackToChat}>
            Back to chat
          </button>
        </div>
      </header>

      {actionError && (
        <div className="voice-error-banner" role="alert">
          <span>{actionError}</span>
          <button type="button" className="btn btn-ghost" onClick={dismissActionError}>
            Dismiss
          </button>
        </div>
      )}

      <div className="voice-layout">
        <section className="voice-library" aria-label="Voice-note library">
          <VoiceRecorder disabled={isUploading} onRecorded={(blob) => void saveRecording(blob)} />

          <div className="voice-import">
            <label className="btn btn-secondary" htmlFor="voice-import-input">
              {isUploading ? "Saving…" : "Import audio file"}
            </label>
            <input
              ref={fileInputRef}
              id="voice-import-input"
              className="voice-file-input"
              type="file"
              accept="audio/*"
              disabled={isUploading}
              onChange={(event) => void handleFile(event.target.files?.[0])}
            />
            <p className="voice-hint">Supported: webm, ogg/opus, wav, mp3, m4a, aac, flac.</p>
            {importError && (
              <p className="voice-error" role="alert">
                {importError}
              </p>
            )}
          </div>

          <div className="voice-list-header">
            <h3>Saved notes</h3>
            {notes.length > 0 && <span className="voice-count">{notes.length}</span>}
          </div>

          {isLoadingList ? (
            <p className="voice-note" role="status">
              Loading voice notes…
            </p>
          ) : listError ? (
            <div className="voice-list-error" role="alert">
              <p>{listError}</p>
              <button type="button" className="btn btn-secondary" onClick={() => void refresh()}>
                Retry
              </button>
            </div>
          ) : notes.length === 0 ? (
            <p className="voice-empty">
              No voice notes yet. Record something or import an audio file.
            </p>
          ) : (
            <ul className="voice-list">
              {notes.map((note) => (
                <li key={note.id}>
                  <button
                    type="button"
                    className={`voice-list-item${note.id === selectedId ? " active" : ""}`}
                    onClick={() => void select(note.id)}
                    aria-current={note.id === selectedId ? "true" : undefined}
                  >
                    <span className="voice-list-title">{note.title}</span>
                    <span className="voice-list-meta">
                      {formatTimestamp(note.created_at)} · {statusLabel(note)}
                      {!note.audio_available && !note.audio_deleted ? " · audio missing" : ""}
                      {note.audio_deleted ? " · audio deleted" : ""}
                    </span>
                    {note.transcript_preview && (
                      <span className="voice-list-preview">{note.transcript_preview}</span>
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="voice-detail-pane" aria-live="polite">
          {isLoadingDetail ? (
            <p className="voice-note" role="status">
              Loading voice note…
            </p>
          ) : detailError ? (
            <div className="voice-list-error" role="alert">
              <p>{detailError}</p>
              {selectedId && (
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={() => void select(selectedId)}
                >
                  Retry
                </button>
              )}
            </div>
          ) : detail ? (
            <VoiceNoteDetail
              key={detail.id}
              note={detail}
              isTranscribing={isTranscribing}
              isSaving={isSaving}
              isSending={isSending}
              isDeleting={isDeleting}
              onTranscribe={(force) => transcribe(detail.id, force)}
              onSaveTranscript={(transcript) => saveTranscript(detail.id, transcript)}
              onSendToChat={() => sendToChat(detail.id)}
              onOpenConversation={onOpenConversation}
              onDeleteAudio={() => removeAudio(detail.id)}
              onDeleteNote={() => removeNote(detail.id)}
            />
          ) : (
            <div className="voice-placeholder">
              <h3>Select a voice note</h3>
              <p>
                Playback, transcription status, and transcript corrections appear here.
                Recordings and transcripts stay in the local data directory.
              </p>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
