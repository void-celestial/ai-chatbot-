import { useEffect, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import type { MemoryCorrectionInput, MemoryDetail as MemoryDetailData } from "../types";
import { formatTimestamp } from "../types";
import {
  canConfirmMemory,
  canDeleteMemory,
  canEditMemory,
  canRejectMemory,
  canSupersedeMemory,
  describeHistory,
  kindLabel,
  originLabel,
} from "../memoryUtils";
import { MemoryStatusBadge } from "./MemoryStatusBadge";

const MAX_STATEMENT = 2_000;
const MAX_SUBJECT = 200;
const MAX_KEYWORDS = 20;
const MAX_KEYWORD_LENGTH = 50;
const MAX_REASON = 500;

type Mode = "view" | "edit" | "confirmReject" | "confirmSupersede" | "confirmDelete";

interface EditErrors {
  statement?: string;
  subject?: string;
  keywords?: string;
  form?: string;
}

interface Props {
  detail: MemoryDetailData | null;
  isLoading: boolean;
  error: string | null;
  isMutating: boolean;
  mutationError: string | null;
  knownConversationIds: ReadonlySet<string>;
  onRetry: () => void;
  onConfirm: (memoryId: string) => Promise<boolean>;
  onReject: (memoryId: string, reason?: string) => Promise<boolean>;
  onSupersede: (memoryId: string, reason?: string) => Promise<boolean>;
  onDelete: (memoryId: string) => Promise<boolean>;
  onCorrect: (memoryId: string, changes: MemoryCorrectionInput) => Promise<boolean>;
  onOpenConversation: (conversationId: string) => void;
  onDismissError: () => void;
}

function parseKeywords(raw: string): string[] {
  const seen = new Set<string>();
  const keywords: string[] = [];
  for (const part of raw.split(",")) {
    const value = part.trim();
    if (!value) continue;
    const key = value.toLocaleLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    keywords.push(value);
  }
  return keywords;
}

function validateForm(statement: string, subject: string, keywords: string[]): EditErrors {
  const errors: EditErrors = {};
  if (!statement.trim()) {
    errors.statement = "Statement cannot be empty.";
  } else if (statement.trim().length > MAX_STATEMENT) {
    errors.statement = `Keep the statement to ${MAX_STATEMENT} characters or fewer.`;
  }
  if (subject.trim().length > MAX_SUBJECT) {
    errors.subject = `Keep the subject to ${MAX_SUBJECT} characters or fewer.`;
  }
  if (keywords.length > MAX_KEYWORDS) {
    errors.keywords = `Use at most ${MAX_KEYWORDS} keywords.`;
  } else if (keywords.some((keyword) => keyword.length > MAX_KEYWORD_LENGTH)) {
    errors.keywords = `Each keyword must be ${MAX_KEYWORD_LENGTH} characters or fewer.`;
  }
  return errors;
}

const HISTORY_LABELS: Record<string, string> = {
  created: "Created",
  corrected: "Updated",
  merged: "Merged",
  conflicted: "Conflict recorded",
};

function historyLabel(event: string): string {
  return HISTORY_LABELS[event] ?? event;
}

export function MemoryDetail({
  detail,
  isLoading,
  error,
  isMutating,
  mutationError,
  knownConversationIds,
  onRetry,
  onConfirm,
  onReject,
  onSupersede,
  onDelete,
  onCorrect,
  onOpenConversation,
  onDismissError,
}: Props) {
  const [mode, setMode] = useState<Mode>("view");
  const [statement, setStatement] = useState("");
  const [subject, setSubject] = useState("");
  const [keywords, setKeywords] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [supersedeReason, setSupersedeReason] = useState("");
  const [errors, setErrors] = useState<EditErrors>({});
  const [statusNote, setStatusNote] = useState<string | null>(null);

  useEffect(() => {
    setMode("view");
    setRejectReason("");
    setSupersedeReason("");
    setErrors({});
    setStatusNote(null);
  }, [detail?.id]);

  // Keep the form prefilled with the latest stored values after a refresh,
  // without clearing the success note that follows a mutation.
  useEffect(() => {
    setStatement(detail?.statement ?? "");
    setSubject(detail?.subject ?? "");
    setKeywords(detail?.keywords.join(", ") ?? "");
  }, [detail?.statement, detail?.subject, detail?.keywords]);

  if (isLoading && !detail) {
    return (
      <section className="memory-detail-pane" aria-label="Memory details" aria-busy="true">
        <p className="memory-empty">Loading memory…</p>
      </section>
    );
  }

  if (error) {
    return (
      <section className="memory-detail-pane" aria-label="Memory details">
        <div className="memory-error" role="alert">
          <p>{error}</p>
          <button type="button" className="btn btn-secondary" onClick={onRetry}>
            Try again
          </button>
        </div>
      </section>
    );
  }

  if (!detail) {
    return (
      <section className="memory-detail-pane" aria-label="Memory details">
        <div className="memory-empty">
          <p>Select a memory to inspect it.</p>
          <p className="memory-muted">
            You can see where it came from, correct it, confirm or reject inferred details, or
            mark it inactive.
          </p>
        </div>
      </section>
    );
  }

  const startEditing = () => {
    setStatement(detail.statement);
    setSubject(detail.subject ?? "");
    setKeywords(detail.keywords.join(", "));
    setErrors({});
    setMode("edit");
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (isMutating) return;
    const parsedKeywords = parseKeywords(keywords);
    const nextErrors = validateForm(statement, subject, parsedKeywords);
    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      return;
    }

    const changes: MemoryCorrectionInput = {};
    const trimmedStatement = statement.trim();
    if (trimmedStatement !== detail.statement) changes.statement = trimmedStatement;
    const subjectValue = subject.trim() || null;
    if (subjectValue !== detail.subject) changes.subject = subjectValue;
    if (
      parsedKeywords.length !== detail.keywords.length ||
      parsedKeywords.some((keyword, index) => keyword !== detail.keywords[index])
    ) {
      changes.keywords = parsedKeywords;
    }
    if (Object.keys(changes).length === 0) {
      setErrors({ form: "Change at least one field before saving." });
      return;
    }

    setErrors({});
    const saved = await onCorrect(detail.id, changes);
    if (saved) {
      setMode("view");
      setStatusNote("Memory updated.");
    }
  };

  const handleConfirm = async () => {
    if (isMutating) return;
    const confirmed = await onConfirm(detail.id);
    if (confirmed) setStatusNote("Memory confirmed.");
  };

  const handleReject = async () => {
    if (isMutating) return;
    const rejected = await onReject(detail.id, rejectReason.trim() || undefined);
    if (rejected) {
      setMode("view");
      setStatusNote("Memory rejected and marked inactive.");
    }
  };

  const handleSupersede = async () => {
    if (isMutating) return;
    const updated = await onSupersede(detail.id, supersedeReason.trim() || undefined);
    if (updated) {
      setMode("view");
      setStatusNote("Memory marked outdated and excluded from replies.");
    }
  };

  const handleDelete = async () => {
    if (isMutating) return;
    await onDelete(detail.id);
  };

  const closePanels = (event: KeyboardEvent<HTMLElement>) => {
    if (event.key === "Escape" && !isMutating) {
      setMode("view");
    }
  };

  const isSecondaryStatus =
    detail.status === "conflicted" ||
    detail.status === "superseded" ||
    detail.status === "orphaned";
  const sourceConversationId = detail.provenance.conversation_id;

  return (
    <section className="memory-detail-pane" aria-labelledby="memory-detail-heading">
      <div className="memory-detail-head">
        <h3 id="memory-detail-heading" className="memory-detail-statement">
          {detail.statement}
        </h3>
        <div className="memory-badges">
          <MemoryStatusBadge status={detail.status} />
          <span className="memory-kind">{kindLabel(detail.kind)}</span>
          <span className="memory-origin">{originLabel(detail.origin)}</span>
        </div>
      </div>

      {detail.status === "review" && (
        <p className="memory-callout">
          This memory was inferred and is awaiting review. It is <strong>not used in replies</strong>{" "}
          until you confirm it.
        </p>
      )}
      {detail.status !== "review" && detail.origin === "inferred" && detail.status !== "deleted" && (
        <p className="memory-callout memory-callout-muted">
          This memory was inferred, not stated directly by you. Confirming it does not make it a
          direct quote.
        </p>
      )}
      {detail.status === "deleted" && (
        <p className="memory-callout">
          This memory is inactive. It is excluded from retrieval and replies. Deletion here is a{" "}
          <strong>soft delete</strong>: the stored record and its history remain.
        </p>
      )}
      {isSecondaryStatus && (
        <p className="memory-callout memory-callout-muted">
          This memory is not used in replies while its status is{" "}
          {detail.status === "orphaned" ? "source missing" : detail.status}.
        </p>
      )}

      <dl className="memory-meta-grid">
        <div>
          <dt>Origin</dt>
          <dd>
            {originLabel(detail.origin)}
            {detail.origin === "inferred" && (
              <span className="memory-muted"> — may be inaccurate</span>
            )}
          </dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>
            {detail.confidence.toFixed(2)}{" "}
            <span className="memory-muted">(extraction certainty, not proof)</span>
          </dd>
        </div>
        <div>
          <dt>Created</dt>
          <dd>
            <time dateTime={detail.created_at}>{formatTimestamp(detail.created_at)}</time>
          </dd>
        </div>
        <div>
          <dt>Updated</dt>
          <dd>
            <time dateTime={detail.updated_at}>{formatTimestamp(detail.updated_at)}</time>
          </dd>
        </div>
        {detail.last_confirmed_at && (
          <div>
            <dt>Last confirmed</dt>
            <dd>
              <time dateTime={detail.last_confirmed_at}>
                {formatTimestamp(detail.last_confirmed_at)}
              </time>
            </dd>
          </div>
        )}
      </dl>

      {detail.subject && (
        <p className="memory-field-line">
          <span className="memory-muted">Subject:</span> {detail.subject}
        </p>
      )}
      {detail.keywords.length > 0 && (
        <p className="memory-field-line">
          <span className="memory-muted">Keywords:</span> {detail.keywords.join(", ")}
        </p>
      )}

      <section className="memory-section" aria-labelledby="memory-sources-heading">
        <h4 id="memory-sources-heading">Sources</h4>
        <p className="memory-muted">
          Memories are linked to the conversation and messages they came from. Source message
          content is not shown here.
        </p>
        <dl className="memory-sources">
          <div>
            <dt>Conversation</dt>
            <dd>
              {sourceConversationId ? (
                knownConversationIds.has(sourceConversationId) ? (
                  <button
                    type="button"
                    className="btn btn-secondary memory-inline-button"
                    onClick={() => onOpenConversation(sourceConversationId)}
                  >
                    Open source conversation
                  </button>
                ) : (
                  <>
                    <code>{sourceConversationId}</code>{" "}
                    <span className="memory-muted">(not in the current conversation list)</span>
                  </>
                )
              ) : (
                <span className="memory-muted">Not available</span>
              )}
            </dd>
          </div>
          <div>
            <dt>Source messages</dt>
            <dd>
              {detail.provenance.message_ids.length > 0 ? (
                <ul className="memory-id-list">
                  {detail.provenance.message_ids.map((messageId) => (
                    <li key={messageId}>
                      <code>{messageId}</code>
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="memory-muted">None available</span>
              )}
            </dd>
          </div>
        </dl>
        {!detail.provenance.valid && (
          <p className="memory-warning">
            The original source is no longer available. This memory is excluded from retrieval
            until it can be linked to a surviving source.
          </p>
        )}
      </section>

      <section className="memory-section" aria-labelledby="memory-history-heading">
        <h4 id="memory-history-heading">History</h4>
        <p className="memory-muted">Newest changes appear first.</p>
        <ol className="memory-history">
          {[...detail.history].reverse().map((entry) => {
            const description = describeHistory(entry.value);
            return (
              <li key={entry.id}>
                <div className="memory-history-head">
                  <strong>{historyLabel(entry.event)}</strong>
                  <time dateTime={entry.created_at}>{formatTimestamp(entry.created_at)}</time>
                </div>
                {description.statusChange && <p>{description.statusChange}</p>}
                {description.reason && (
                  <p className="memory-muted">Reason: {description.reason}</p>
                )}
                {description.before !== undefined && description.after !== undefined && (
                  <div className="memory-history-diff">
                    <p>
                      <span className="memory-muted">Before:</span> {description.before}
                    </p>
                    <p>
                      <span className="memory-muted">After:</span> {description.after}
                    </p>
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      </section>

      {statusNote && (
        <p className="memory-note" role="status">
          {statusNote}
        </p>
      )}
      {mutationError && (
        <div className="memory-error" role="alert">
          <p>{mutationError}</p>
          <button type="button" className="btn btn-ghost" onClick={onDismissError}>
            Dismiss
          </button>
        </div>
      )}

      {mode === "view" && (
        <div className="memory-actions">
          {canEditMemory(detail.status) && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={startEditing}
              disabled={isMutating}
            >
              Edit…
            </button>
          )}
          {canConfirmMemory(detail.status) && (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void handleConfirm()}
              disabled={isMutating}
            >
              Confirm memory
            </button>
          )}
          {canRejectMemory(detail.status) && (
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => setMode("confirmReject")}
              disabled={isMutating}
            >
              Reject…
            </button>
          )}
          {canSupersedeMemory(detail.status) && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setMode("confirmSupersede")}
              disabled={isMutating}
            >
              Mark outdated…
            </button>
          )}
          {canDeleteMemory(detail.status) && (
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => setMode("confirmDelete")}
              disabled={isMutating}
            >
              Delete…
            </button>
          )}
        </div>
      )}

      {mode === "edit" && (
        <form className="memory-form" onSubmit={(event) => void handleSubmit(event)} noValidate>
          <div className="memory-form-field">
            <label htmlFor="memory-statement">Statement</label>
            <textarea
              id="memory-statement"
              value={statement}
              maxLength={MAX_STATEMENT}
              rows={4}
              aria-invalid={errors.statement ? true : undefined}
              aria-describedby={errors.statement ? "memory-statement-error" : undefined}
              onChange={(event) => setStatement(event.target.value)}
            />
            <p className="memory-field-hint">
              {statement.trim().length}/{MAX_STATEMENT} characters
            </p>
            {errors.statement && (
              <p className="memory-field-error" id="memory-statement-error">
                {errors.statement}
              </p>
            )}
          </div>

          <div className="memory-form-field">
            <label htmlFor="memory-subject">Subject (optional)</label>
            <input
              id="memory-subject"
              type="text"
              value={subject}
              maxLength={MAX_SUBJECT}
              aria-invalid={errors.subject ? true : undefined}
              onChange={(event) => setSubject(event.target.value)}
            />
            {errors.subject && <p className="memory-field-error">{errors.subject}</p>}
          </div>

          <div className="memory-form-field">
            <label htmlFor="memory-keywords">Keywords (comma-separated)</label>
            <input
              id="memory-keywords"
              type="text"
              value={keywords}
              aria-invalid={errors.keywords ? true : undefined}
              onChange={(event) => setKeywords(event.target.value)}
            />
            <p className="memory-field-hint">
              Up to {MAX_KEYWORDS} keywords, {MAX_KEYWORD_LENGTH} characters each. Duplicates are
              ignored.
            </p>
            {errors.keywords && <p className="memory-field-error">{errors.keywords}</p>}
          </div>

          {errors.form && (
            <p className="memory-field-error" role="alert">
              {errors.form}
            </p>
          )}

          <div className="memory-actions">
            <button type="submit" className="btn btn-primary" disabled={isMutating}>
              {isMutating ? "Saving…" : "Save changes"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setMode("view")}
              disabled={isMutating}
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {mode === "confirmReject" && (
        <section
          className="memory-confirm"
          aria-labelledby="memory-reject-heading"
          onKeyDown={closePanels}
        >
          <h4 id="memory-reject-heading">Reject this memory?</h4>
          <p>
            Rejecting marks the memory inactive. It will no longer be used in replies. The stored
            record and its history remain.
          </p>
          <div className="memory-form-field">
            <label htmlFor="memory-reject-reason">Reason (optional)</label>
            <textarea
              id="memory-reject-reason"
              value={rejectReason}
              maxLength={MAX_REASON}
              rows={2}
              onChange={(event) => setRejectReason(event.target.value)}
            />
          </div>
          <div className="memory-actions">
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => void handleReject()}
              disabled={isMutating}
            >
              {isMutating ? "Rejecting…" : "Reject memory"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setMode("view")}
              disabled={isMutating}
              autoFocus
            >
              Cancel
            </button>
          </div>
        </section>
      )}

      {mode === "confirmSupersede" && (
        <section
          className="memory-confirm"
          aria-labelledby="memory-supersede-heading"
          onKeyDown={closePanels}
        >
          <h4 id="memory-supersede-heading">Mark this memory outdated?</h4>
          <p>
            It will be kept for audit and export, but excluded from replies from now on. Use this
            when circumstances have changed, for example a move, a finished goal, or a corrected
            fact. The history is retained.
          </p>
          <div className="memory-form-field">
            <label htmlFor="memory-supersede-reason">Reason (optional)</label>
            <textarea
              id="memory-supersede-reason"
              value={supersedeReason}
              maxLength={MAX_REASON}
              rows={2}
              onChange={(event) => setSupersedeReason(event.target.value)}
            />
          </div>
          <div className="memory-actions">
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void handleSupersede()}
              disabled={isMutating}
            >
              {isMutating ? "Updating…" : "Mark outdated"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setMode("view")}
              disabled={isMutating}
              autoFocus
            >
              Cancel
            </button>
          </div>
        </section>
      )}

      {mode === "confirmDelete" && (
        <section
          className="memory-confirm"
          aria-labelledby="memory-delete-heading"
          onKeyDown={closePanels}
        >
          <h4 id="memory-delete-heading">Delete this memory?</h4>
          <p>
            This is a <strong>soft delete</strong>: the memory is marked inactive and excluded from
            replies, but the stored record and its history may remain. Source conversations and
            messages are not deleted.
          </p>
          <div className="memory-actions">
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => void handleDelete()}
              disabled={isMutating}
            >
              {isMutating ? "Deleting…" : "Delete memory"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setMode("view")}
              disabled={isMutating}
              autoFocus
            >
              Cancel
            </button>
          </div>
        </section>
      )}
    </section>
  );
}
