import type { MemorySummary } from "../types";
import { formatTimestamp } from "../types";
import { kindLabel, originLabel } from "../memoryUtils";
import { MemoryStatusBadge } from "./MemoryStatusBadge";

interface Props {
  items: MemorySummary[];
  total: number;
  limit: number;
  offset: number;
  selectedId: string | null;
  isLoading: boolean;
  error: string | null;
  hasFilters: boolean;
  onSelect: (memoryId: string) => void;
  onRetry: () => void;
  onPrevious: () => void;
  onNext: () => void;
}

export function MemoryList({
  items,
  total,
  limit,
  offset,
  selectedId,
  isLoading,
  error,
  hasFilters,
  onSelect,
  onRetry,
  onPrevious,
  onNext,
}: Props) {
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + limit, total);
  const hasPrevious = offset > 0;
  const hasNext = offset + limit < total;

  return (
    <section className="memory-list-pane" aria-label="Saved memories" aria-busy={isLoading}>
      <div className="memory-list-summary" role="status">
        {isLoading
          ? "Loading memories…"
          : error
            ? "Memories could not be loaded."
            : total === 0
              ? "No memories"
              : `${total} ${total === 1 ? "memory" : "memories"}${
                  total > 0 ? ` · showing ${start}–${end}` : ""
                }`}
      </div>

      {error && (
        <div className="memory-error" role="alert">
          <p>{error}</p>
          <button type="button" className="btn btn-secondary" onClick={onRetry}>
            Try again
          </button>
        </div>
      )}

      {isLoading && items.length === 0 && (
        <p className="memory-empty">Loading saved memories…</p>
      )}

      {!isLoading && !error && items.length === 0 && (
        <div className="memory-empty">
          <p>{hasFilters ? "No memories match these filters." : "No memories have been saved yet."}</p>
          <p className="memory-muted">
            {hasFilters
              ? "Try clearing a filter or checking another status."
              : "Memories appear here after the companion extracts them from a conversation."}
          </p>
        </div>
      )}

      {items.length > 0 && (
        <ul className="memory-list">
          {items.map((memory) => {
            const selected = memory.id === selectedId;
            return (
              <li key={memory.id}>
                <button
                  type="button"
                  className={`memory-item${selected ? " selected" : ""}`}
                  aria-current={selected ? "true" : undefined}
                  onClick={() => onSelect(memory.id)}
                >
                  <span className="memory-item-statement">{memory.statement}</span>
                  <span className="memory-item-meta">
                    <MemoryStatusBadge status={memory.status} />
                    <span>{kindLabel(memory.kind)}</span>
                    <span>{originLabel(memory.origin)}</span>
                    {!memory.provenance.valid && (
                      <span className="memory-source-warning">No valid source</span>
                    )}
                    <time dateTime={memory.updated_at}>
                      {formatTimestamp(memory.updated_at)}
                    </time>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {total > 0 && (
        <nav className="memory-pagination" aria-label="Memory pages">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={onPrevious}
            disabled={!hasPrevious || isLoading}
          >
            Previous
          </button>
          <span className="memory-muted">
            Showing {start}–{end} of {total}
          </span>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={onNext}
            disabled={!hasNext || isLoading}
          >
            Next
          </button>
        </nav>
      )}
    </section>
  );
}
