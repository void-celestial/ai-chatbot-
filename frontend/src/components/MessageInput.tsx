import { useRef, useState } from "react";

interface Props {
  disabled: boolean;
  isGenerating: boolean;
  onSend: (content: string) => void;
  onStop: () => void;
}

const MAX_LEN = 20_000;

export function MessageInput({ disabled, isGenerating, onSend, onStop }: Props) {
  const [draft, setDraft] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const trimmed = draft.trim();
  const tooLong = trimmed.length > MAX_LEN;
  const canSend = !disabled && !isGenerating && trimmed.length > 0 && !tooLong;

  const submit = () => {
    if (!canSend) return;
    onSend(trimmed);
    setDraft("");
    requestAnimationFrame(() => textareaRef.current?.focus());
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  };

  return (
    <div className="message-input">
      <div className="composer">
        <textarea
          ref={textareaRef}
          value={draft}
          onChange={(event) => setDraft(event.target.value.slice(0, MAX_LEN))}
          onKeyDown={handleKeyDown}
          placeholder={
            disabled ? "Start a new conversation to write…" : "Message your companion…"
          }
          rows={2}
          aria-label="Message to send"
          maxLength={MAX_LEN}
          disabled={disabled}
        />

        <div className="composer-footer">
          <span className="composer-hint">
            Enter to send · Shift + Enter for a new line
          </span>

          <div className="input-actions">
            {isGenerating ? (
              <button type="button" className="btn btn-secondary stop-button" onClick={onStop}>
                <span className="stop-glyph" aria-hidden="true" />
                Stop
              </button>
            ) : (
              <button
                type="button"
                className="btn btn-primary send-button"
                onClick={submit}
                disabled={!canSend}
                title={tooLong ? "Message is too long" : "Send (Enter)"}
              >
                <span>Send</span>
                <svg
                  width="15"
                  height="15"
                  viewBox="0 0 24 24"
                  aria-hidden="true"
                  focusable="false"
                >
                  <path
                    d="M5 12h13.5M12.6 6.2 18.9 12l-6.3 5.8"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
