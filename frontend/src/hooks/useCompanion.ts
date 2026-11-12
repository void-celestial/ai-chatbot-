import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiRequestError,
  createConversation,
  deleteConversation as deleteConversationRequest,
  listConversations,
  listMessages,
  streamChat,
} from "../api/client";
import type { ApiError, ChatEvent, Conversation, Message } from "../types";

interface StreamedReply {
  messageId: string;
  content: string;
}

/**
 * Turn a failed request into one honest, actionable message.
 *
 * A stopped backend is the most common local failure and used to surface as a
 * bare `TypeError: Failed to fetch`, which the UI then paired with a false
 * "no conversations yet" empty state. Network failures now say what to do,
 * while API errors keep the backend's own (already sanitized) detail.
 */
function describeRequestFailure(error: unknown, fallback: string): ApiError {
  if (error instanceof ApiRequestError) {
    return { code: error.code, message: error.message };
  }
  if (error instanceof TypeError) {
    return {
      code: "backend_unreachable",
      message:
        "Could not reach the companion backend. Start it with " +
        "`py -m uvicorn backend.app.main:app --port 8000`, then try again.",
    };
  }
  const message = error instanceof Error ? error.message.trim() : "";
  return { code: "unknown", message: message || fallback };
}

export function useCompanion() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [streamedReply, setStreamedReply] = useState<StreamedReply | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [isLoadingMessages, setIsLoadingMessages] = useState(false);
  const [backendError, setBackendError] = useState<ApiError | null>(null);
  const [conversationsError, setConversationsError] = useState<string | null>(null);
  const [messagesError, setMessagesError] = useState<string | null>(null);
  const [isLoadingConversations, setIsLoadingConversations] = useState(true);
  const [canRetry, setCanRetry] = useState(false);
  const [isDeletingConversation, setIsDeletingConversation] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  // Monotonic token so a slow request for conversation A can never render into
  // conversation B after the user has already switched.
  const messagesRequestRef = useRef(0);
  const activeIdRef = useRef<string | null>(null);

  useEffect(() => {
    activeIdRef.current = activeId;
  }, [activeId]);

  /** Abandon any in-flight message load without leaving a permanent spinner. */
  const invalidateMessageLoad = useCallback(() => {
    messagesRequestRef.current += 1;
    setIsLoadingMessages(false);
  }, []);

  const resetStreamState = useCallback(() => {
    setStreamedReply(null);
    setIsGenerating(false);
    setCanRetry(false);
    abortRef.current = null;
  }, []);

  const handleEvent = useCallback(
    (event: ChatEvent) => {
      switch (event.type) {
        case "user_message":
          setMessages((current) => [...current, event.message]);
          break;
        case "assistant_started":
          setStreamedReply({ messageId: event.message_id, content: "" });
          break;
        case "delta":
          setStreamedReply((current) =>
            current ? { ...current, content: current.content + event.content } : current,
          );
          break;
        case "done":
          setMessages((current) => {
            // Replace anything we streamed locally with the canonical record.
            return [...current, event.message];
          });
          setStreamedReply(null);
          break;
        case "error":
          setBackendError({ code: event.code, message: event.message });
          setCanRetry(true);
          setStreamedReply(null);
          break;
      }
    },
    [],
  );

  const loadMessages = useCallback(async (conversationId: string) => {
    messagesRequestRef.current += 1;
    const requestId = messagesRequestRef.current;
    setIsLoadingMessages(true);
    setMessagesError(null);
    setBackendError(null);
    setStreamedReply(null);
    setCanRetry(false);
    try {
      const list = await listMessages(conversationId);
      if (messagesRequestRef.current !== requestId) return;
      setMessages(list);
    } catch (error) {
      if (messagesRequestRef.current !== requestId) return;
      // Never leave an empty transcript standing in for a failed request.
      setMessages([]);
      setMessagesError(
        describeRequestFailure(error, "Could not load this conversation's messages.")
          .message,
      );
      setCanRetry(false);
    } finally {
      if (messagesRequestRef.current === requestId) setIsLoadingMessages(false);
    }
  }, []);

  const refreshConversations = useCallback(
    async (selectFirst = false) => {
      setIsLoadingConversations(true);
      try {
        const list = await listConversations();
        setConversations(list);
        setConversationsError(null);
        if (selectFirst) {
          // The first conversation is selected on startup, so its messages must
          // be fetched too; otherwise the transcript showed an empty state for a
          // conversation that does have history.
          const nextId = activeIdRef.current ?? list[0]?.id ?? null;
          if (nextId) {
            const isNewSelection = nextId !== activeIdRef.current;
            activeIdRef.current = nextId;
            setActiveId(nextId);
            if (isNewSelection) void loadMessages(nextId);
          }
        }
        return list;
      } catch (error) {
        // Keep whatever is already on screen and report the failure once, in the
        // sidebar, instead of pretending the history is empty.
        setConversationsError(
          describeRequestFailure(error, "Could not load your conversations.").message,
        );
        return [] as Conversation[];
      } finally {
        setIsLoadingConversations(false);
      }
    },
    [loadMessages],
  );

  const selectConversation = useCallback(
    (conversationId: string) => {
      if (isGenerating) abortRef.current?.abort();
      resetStreamState();
      setActiveId(conversationId);
      void loadMessages(conversationId);
    },
    [isGenerating, loadMessages, resetStreamState],
  );

  const deleteConversation = useCallback(
    async (conversationId: string): Promise<boolean> => {
      if (isDeletingConversation) return false;
      setIsDeletingConversation(true);
      setBackendError(null);
      try {
        await deleteConversationRequest(conversationId);
      } catch (error) {
        setBackendError(
          describeRequestFailure(error, "Could not delete the conversation."),
        );
        setIsDeletingConversation(false);
        return false;
      }

      const remaining = conversations.filter((item) => item.id !== conversationId);
      setConversations(remaining);
      if (conversationId === activeId) {
        resetStreamState();
        invalidateMessageLoad();
        setMessages([]);
        setMessagesError(null);
        const nextId = remaining[0]?.id ?? null;
        setActiveId(nextId);
        if (nextId) void loadMessages(nextId);
      }
      setIsDeletingConversation(false);
      // Reconcile with the server (ordering, counts) without blocking the UI.
      void refreshConversations();
      return true;
    },
    [
      activeId,
      conversations,
      invalidateMessageLoad,
      isDeletingConversation,
      loadMessages,
      refreshConversations,
      resetStreamState,
    ],
  );

  const newConversation = useCallback(async () => {
    if (isGenerating) abortRef.current?.abort();
    resetStreamState();
    invalidateMessageLoad();
    try {
      const conversation = await createConversation();
      setConversations((current) => [conversation, ...current]);
      setActiveId(conversation.id);
      // A load that was in flight for the previous conversation is now stale.
      invalidateMessageLoad();
      setMessages([]);
      setMessagesError(null);
      setBackendError(null);
      return conversation;
    } catch (error) {
      setBackendError(describeRequestFailure(error, "Could not create a conversation."));
      return null;
    }
  }, [invalidateMessageLoad, isGenerating, resetStreamState]);

  const runStream = useCallback(
    async (path: string, body: unknown) => {
      const controller = new AbortController();
      abortRef.current = controller;
      setIsGenerating(true);
      // Whatever was being loaded must not land in the middle of this stream.
      invalidateMessageLoad();
      setMessagesError(null);
      try {
        await streamChat(path, body, handleEvent, controller.signal);
        void refreshConversations();
      } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") {
          // User stopped generation; the backend may have persisted a partial
          // reply. Resync with the server state.
          void refreshConversations();
          if (activeId) await loadMessages(activeId);
        } else if (error instanceof ApiRequestError) {
          setBackendError({ code: error.code, message: error.message });
          setCanRetry(true);
        } else {
          setBackendError({
            code: "network_error",
            message:
              "Could not reach the companion backend. Start it with " +
              "`py -m uvicorn backend.app.main:app --port 8000`, then reload this page.",
          });
          setCanRetry(true);
        }
      } finally {
        abortRef.current = null;
        setIsGenerating(false);
        setStreamedReply(null);
      }
    },
    [activeId, handleEvent, invalidateMessageLoad, loadMessages, refreshConversations],
  );

  const sendMessage = useCallback(
    async (content: string) => {
      if (!activeId || isGenerating || !content.trim()) return;
      setBackendError(null);
      setCanRetry(false);
      await runStream(
        `/api/conversations/${encodeURIComponent(activeId)}/messages`,
        { content },
      );
    },
    [activeId, isGenerating, runStream],
  );

  const retryGeneration = useCallback(async () => {
    if (!activeId || isGenerating) return;
    setBackendError(null);
    setCanRetry(false);
    await runStream(`/api/conversations/${encodeURIComponent(activeId)}/retry`, undefined);
  }, [activeId, isGenerating, runStream]);

  const stopGeneration = useCallback(() => {
    if (!isGenerating) return;
    abortRef.current?.abort();
  }, [isGenerating]);

  const dismissError = useCallback(() => {
    setBackendError(null);
  }, []);

  const retryLoadConversations = useCallback(() => {
    void refreshConversations(true);
  }, [refreshConversations]);

  const retryLoadMessages = useCallback(() => {
    if (activeId) void loadMessages(activeId);
  }, [activeId, loadMessages]);

  const activeConversation = conversations.find((c) => c.id === activeId) ?? null;

  return {
    conversations,
    activeConversation,
    activeId,
    messages,
    streamedReply,
    isGenerating,
    isLoadingMessages,
    isLoadingConversations,
    conversationsError,
    messagesError,
    retryLoadMessages,
    isDeletingConversation,
    backendError,
    canRetry,
    refreshConversations,
    retryLoadConversations,
    selectConversation,
    deleteConversation,
    newConversation,
    sendMessage,
    retryGeneration,
    stopGeneration,
    dismissError,
  };
}
