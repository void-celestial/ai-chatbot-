import { useEffect, useRef } from "react";
import type { Message } from "../types";
import { formatTimestamp } from "../types";
import { kindLabel, originLabel, statusLabel } from "../memoryUtils";
import { CompanionEmblem } from "./CompanionEmblem";

interface Props {
  messages: Message[];
  streamedContent: string | null;
  isLoading: boolean;
  conversationTitle: string;
  /** Set when retrieving this conversation's messages failed. */
  errorMessage?: string | null;
  onRetry?: () => void;
}

export function MessageList({
  messages,
  streamedContent,
  isLoading,
  conversationTitle,
  errorMessage = null,
  onRetry,
}: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const lastCount = useRef(0);
  const lastStreamLength = useRef(0);

  useEffect(() => {
    const grew = messages.length !== lastCount.current || (streamedContent?.length ?? 0) !== lastStreamLength.current;
    lastCount.current = messages.length;
    lastStreamLength.current = streamedContent?.length ?? 0;
    if (grew) bottomRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
  }, [messages.length, streamedContent]);

  if (isLoading) {
    return (
      <div className="chat-empty chat-empty-loading" role="status">
        <CompanionEmblem size={44} className="empty-emblem" />
        <p className="chat-empty-title">Loading conversation…</p>
      </div>
    );
  }

  if (errorMessage && messages.length === 0) {
    return (
      <div className="chat-empty chat-empty-error" role="alert">
        <CompanionEmblem size={48} className="empty-emblem" />
        <h3 className="chat-empty-title">Couldn't load this conversation.</h3>
        <p>{errorMessage}</p>
        {onRetry && (
          <button type="button" className="btn btn-secondary btn-sm" onClick={onRetry}>
            Try again
          </button>
        )}
      </div>
    );
  }

  if (messages.length === 0 && streamedContent === null) {
    return (
      <div className="chat-empty">
        <CompanionEmblem size={56} className="empty-emblem" />
        <h3 className="chat-empty-title">This conversation is just beginning.</h3>
        <p>
          Say hello, share what's on your mind, or ask anything — it all stays on this
          computer.
        </p>
      </div>
    );
  }

  return (
    <div className="message-list" aria-label={`Conversation: ${conversationTitle}`}>
      {messages.map((message) => {
        const interrupted = message.generation_status === "interrupted";
        return (
          <article
            key={message.id}
            className={`message message-${message.role}${interrupted ? " message-interrupted" : ""}`}
          >
            {message.role === "assistant" && (
              <CompanionEmblem size={30} className="message-avatar" />
            )}

            <div className="message-content">
              <header className="message-meta">
                <span className="message-author">
                  {message.role === "user" ? "You" : "Companion"}
                  {interrupted ? " (stopped)" : ""}
                </span>
                <time className="message-time" dateTime={message.created_at}>
                  {formatTimestamp(message.created_at)}
                </time>
              </header>

              <div className="message-bubble">
                <div className="message-body">{message.content}</div>

                {message.role === "assistant" &&
                  (message.metadata?.memory_evidence?.length ?? 0) > 0 && (
                    <details className="message-memory">
                      <summary>
                        Memory used ({message.metadata?.memory_evidence?.length})
                      </summary>
                      <ul className="message-memory-list">
                        {message.metadata?.memory_evidence?.map((item) => (
                          <li key={item.id}>
                            <p className="message-memory-statement">{item.statement}</p>
                            <p className="message-memory-meta">
                              {kindLabel(item.kind)} · {originLabel(item.origin)} ·{" "}
                              {statusLabel(item.status)}
                            </p>
                            {item.source_conversation_id && (
                              <p className="message-memory-source">
                                Source conversation: <code>{item.source_conversation_id}</code>
                              </p>
                            )}
                            {item.source_message_ids.length > 0 && (
                              <p className="message-memory-source">
                                Source messages:{" "}
                                <code>{item.source_message_ids.join(", ")}</code>
                              </p>
                            )}
                          </li>
                        ))}
                      </ul>
                      <p className="message-memory-note">
                        These saved records were supplied as evidence for this reply. They can be
                        incomplete or wrong; your corrections take precedence.
                      </p>
                    </details>
                  )}
              </div>
            </div>
          </article>
        );
      })}

      {streamedContent !== null && (
        <article className="message message-assistant message-streaming" aria-live="polite">
          <CompanionEmblem size={30} className="message-avatar" />
          <div className="message-content">
            <header className="message-meta">
              <span className="message-author">Companion</span>
              <span className="streaming-note">
                <span className="typing-dots" aria-hidden="true">
                  <span />
                  <span />
                  <span />
                </span>
                writing…
              </span>
            </header>
            <div className="message-bubble">
              <div className="message-body">
                {streamedContent}
                <span className="streaming-cursor" aria-hidden="true" />
              </div>
            </div>
          </div>
        </article>
      )}
      <div ref={bottomRef} />
    </div>
  );
}
