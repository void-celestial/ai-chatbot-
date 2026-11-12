import { useCallback, useEffect, useRef, useState } from "react";
import {
  deleteVoiceNote,
  deleteVoiceNoteAudio,
  getVoiceNote,
  listVoiceNotes,
  sendVoiceNoteToChat,
  transcribeVoiceNote,
  updateVoiceNote,
  uploadVoiceNote,
} from "../api/client";
import type { VoiceNote, VoiceNoteSummary, VoiceNoteToChatResult } from "../types";

function messageOf(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  return fallback;
}

export function useVoiceNotes() {
  const [notes, setNotes] = useState<VoiceNoteSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<VoiceNote | null>(null);
  const [isLoadingList, setIsLoadingList] = useState(true);
  const [isLoadingDetail, setIsLoadingDetail] = useState(false);
  const [listError, setListError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setIsLoadingList(true);
    try {
      const items = await listVoiceNotes();
      if (!mounted.current) return;
      setNotes(items);
      setListError(null);
    } catch (error) {
      if (!mounted.current) return;
      setListError(messageOf(error, "Could not load voice notes."));
    } finally {
      if (mounted.current && !quiet) setIsLoadingList(false);
    }
  }, []);

  const select = useCallback(async (noteId: string, quiet = false) => {
    setSelectedId(noteId);
    if (!quiet) setIsLoadingDetail(true);
    try {
      const note = await getVoiceNote(noteId);
      if (!mounted.current) return;
      setDetail(note);
      setDetailError(null);
    } catch (error) {
      if (!mounted.current) return;
      setDetailError(messageOf(error, "Could not load this voice note."));
    } finally {
      if (mounted.current && !quiet) setIsLoadingDetail(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Refresh while any transcription is processing so the UI leaves the
  // transient state without a manual reload. Local polling only.
  useEffect(() => {
    const active = notes.some((note) => note.transcript_status === "processing");
    if (!active) return;
    const interval = window.setInterval(() => {
      void refresh(true);
      if (selectedId) void select(selectedId, true);
    }, 2000);
    return () => window.clearInterval(interval);
  }, [notes, refresh, select, selectedId]);

  const upload = useCallback(
    async (audio: Blob, options: { title?: string; contentType?: string } = {}) => {
      if (isUploading) return null;
      setIsUploading(true);
      setActionError(null);
      try {
        const note = await uploadVoiceNote(audio, options);
        await refresh(true);
        setSelectedId(note.id);
        setDetail(note);
        setDetailError(null);
        return note;
      } catch (error) {
        if (mounted.current) {
          setActionError(messageOf(error, "Could not save the recording."));
        }
        return null;
      } finally {
        if (mounted.current) setIsUploading(false);
      }
    },
    [isUploading, refresh],
  );

  const transcribe = useCallback(
    async (noteId: string, force = false) => {
      if (isTranscribing) return false;
      setIsTranscribing(true);
      setActionError(null);
      try {
        const note = await transcribeVoiceNote(noteId, force);
        if (mounted.current) setDetail(note);
        await refresh(true);
        return true;
      } catch (error) {
        if (mounted.current) {
          setActionError(messageOf(error, "Could not start transcription."));
        }
        return false;
      } finally {
        if (mounted.current) setIsTranscribing(false);
      }
    },
    [isTranscribing, refresh],
  );

  const saveTranscript = useCallback(
    async (noteId: string, transcript: string) => {
      if (isSaving) return false;
      setIsSaving(true);
      setActionError(null);
      try {
        const note = await updateVoiceNote(noteId, { transcript });
        if (mounted.current) {
          setDetail(note);
          setDetailError(null);
        }
        await refresh(true);
        return true;
      } catch (error) {
        if (mounted.current) {
          setActionError(messageOf(error, "Could not save the transcript."));
        }
        return false;
      } finally {
        if (mounted.current) setIsSaving(false);
      }
    },
    [isSaving, refresh],
  );

  const sendToChat = useCallback(
    async (noteId: string): Promise<VoiceNoteToChatResult | null> => {
      if (isSending) return null;
      setIsSending(true);
      setActionError(null);
      try {
        const result = await sendVoiceNoteToChat(noteId);
        await refresh(true);
        return result;
      } catch (error) {
        if (mounted.current) {
          setActionError(messageOf(error, "Could not use the transcript."));
        }
        return null;
      } finally {
        if (mounted.current) setIsSending(false);
      }
    },
    [isSending, refresh],
  );

  const removeAudio = useCallback(
    async (noteId: string) => {
      if (isDeleting) return false;
      setIsDeleting(true);
      setActionError(null);
      try {
        await deleteVoiceNoteAudio(noteId);
        await select(noteId, true);
        await refresh(true);
        return true;
      } catch (error) {
        if (mounted.current) {
          setActionError(messageOf(error, "Could not delete the audio."));
        }
        return false;
      } finally {
        if (mounted.current) setIsDeleting(false);
      }
    },
    [isDeleting, refresh, select],
  );

  const removeNote = useCallback(
    async (noteId: string) => {
      if (isDeleting) return false;
      setIsDeleting(true);
      setActionError(null);
      try {
        await deleteVoiceNote(noteId);
        if (mounted.current) {
          setDetail(null);
          setSelectedId(null);
          setDetailError(null);
        }
        await refresh(true);
        return true;
      } catch (error) {
        if (mounted.current) {
          setActionError(messageOf(error, "Could not delete the voice note."));
        }
        return false;
      } finally {
        if (mounted.current) setIsDeleting(false);
      }
    },
    [isDeleting, refresh],
  );

  return {
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
    dismissActionError: () => setActionError(null),
  };
}
