import { useMemo } from "react";
import { useMemories } from "../hooks/useMemories";
import type {
  Conversation,
  MemoryKind,
  MemoryOrigin,
  MemoryStatus,
} from "../types";
import { MemoryDetail } from "./MemoryDetail";
import { MemoryList } from "./MemoryList";

interface Props {
  conversations: Conversation[];
  onBackToChat: () => void;
  onOpenConversation: (conversationId: string) => void;
}

const STATUS_OPTIONS: { value: MemoryStatus; label: string }[] = [
  { value: "review", label: "Needs review" },
  { value: "accepted", label: "Active" },
  { value: "confirmed", label: "Confirmed" },
  { value: "deleted", label: "Inactive (deleted)" },
  { value: "conflicted", label: "Conflicted" },
  { value: "superseded", label: "Superseded" },
  { value: "orphaned", label: "Source missing" },
];

const KIND_OPTIONS: { value: MemoryKind; label: string }[] = [
  { value: "fact", label: "Fact" },
  { value: "preference", label: "Preference" },
  { value: "goal", label: "Goal" },
  { value: "event", label: "Event" },
  { value: "person", label: "Person" },
  { value: "other", label: "Other" },
];

const ORIGIN_OPTIONS: { value: MemoryOrigin; label: string }[] = [
  { value: "user_stated", label: "Stated by you" },
  { value: "inferred", label: "Inferred" },
];

function optionalStatus(value: string): MemoryStatus | undefined {
  return value ? (value as MemoryStatus) : undefined;
}

function optionalKind(value: string): MemoryKind | undefined {
  return value ? (value as MemoryKind) : undefined;
}

function optionalOrigin(value: string): MemoryOrigin | undefined {
  return value ? (value as MemoryOrigin) : undefined;
}

export function MemoryView({ conversations, onBackToChat, onOpenConversation }: Props) {
  const memory = useMemories();
  const knownConversationIds = useMemo(
    () => new Set(conversations.map((conversation) => conversation.id)),
    [conversations],
  );
  const hasFilters = Boolean(memory.filters.status || memory.filters.kind || memory.filters.origin);
  const reviewing = memory.filters.status === "review";

  return (
    <main className="memory-pane">
      <header className="memory-header">
        <div>
          <h2 className="memory-title">Memories</h2>
          <p className="memory-subtitle">
            Review what your companion remembers. Inferred memories are excluded from replies until
            you confirm them.
          </p>
        </div>
        <button type="button" className="btn btn-secondary" onClick={onBackToChat}>
          Back to chat
        </button>
      </header>

      <div className="memory-toolbar">
        <div className="memory-filters">
          <div className="memory-filter">
            <label htmlFor="memory-filter-status">Status</label>
            <select
              id="memory-filter-status"
              value={memory.filters.status ?? ""}
              onChange={(event) =>
                memory.setFilters({
                  ...memory.filters,
                  status: optionalStatus(event.target.value),
                })
              }
            >
              <option value="">All statuses</option>
              {STATUS_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              )              )}
            </select>
          </div>

          <div className="memory-filter">
            <label htmlFor="memory-filter-kind">Kind</label>
            <select
              id="memory-filter-kind"
              value={memory.filters.kind ?? ""}
              onChange={(event) =>
                memory.setFilters({
                  ...memory.filters,
                  kind: optionalKind(event.target.value),
                })
              }
            >
              <option value="">All kinds</option>
              {KIND_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              )              )}
            </select>
          </div>

          <div className="memory-filter">
            <label htmlFor="memory-filter-origin">Origin</label>
            <select
              id="memory-filter-origin"
              value={memory.filters.origin ?? ""}
              onChange={(event) =>
                memory.setFilters({
                  ...memory.filters,
                  origin: optionalOrigin(event.target.value),
                })
              }
            >
              <option value="">All origins</option>
              {ORIGIN_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              )              )}
            </select>
          </div>
        </div>

        <div className="memory-toolbar-actions">
          <button
            type="button"
            className={`btn ${reviewing ? "btn-primary" : "btn-secondary"}`}
            onClick={() =>
              memory.setFilters({ ...memory.filters, status: reviewing ? undefined : "review" })
            }
          >
            Needs review
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => memory.setFilters({})}
            disabled={!hasFilters}
          >
            Clear filters
          </button>
          <button
            type="button"
            className="btn btn-ghost"
            onClick={memory.reloadList}
            disabled={memory.isLoading || memory.isMutating}
          >
            Refresh
          </button>
        </div>
      </div>

      <div className="memory-layout">
        <MemoryList
          items={memory.items}
          total={memory.total}
          limit={memory.limit}
          offset={memory.offset}
          selectedId={memory.selectedId}
          isLoading={memory.isLoading}
          error={memory.listError}
          hasFilters={hasFilters}
          onSelect={memory.selectMemory}
          onRetry={memory.reloadList}
          onPrevious={memory.goToPreviousPage}
          onNext={memory.goToNextPage}
        />

        <MemoryDetail
          detail={memory.detail}
          isLoading={memory.isDetailLoading}
          error={memory.detailError}
          isMutating={memory.isMutating}
          mutationError={memory.mutationError}
          knownConversationIds={knownConversationIds}
          onRetry={memory.refreshDetail}
          onConfirm={memory.confirm}
          onReject={memory.reject}
          onSupersede={memory.supersede}
          onDelete={memory.remove}
          onCorrect={memory.correct}
          onOpenConversation={onOpenConversation}
          onDismissError={memory.clearMutationError}
        />
      </div>

      <p className="memory-footnote">
        Deleting a memory marks it inactive: it is excluded from replies, but the stored record and
        history remain. Source conversations and messages are never deleted.
      </p>
    </main>
  );
}
