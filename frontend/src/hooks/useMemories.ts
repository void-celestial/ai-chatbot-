import { useCallback, useEffect, useState } from "react";
import {
  confirmMemory,
  correctMemory,
  deleteMemory,
  getMemory,
  listMemories,
  rejectMemory,
  supersedeMemory,
} from "../api/client";
import { MEMORY_FILTER_LIMIT, errorMessage } from "../memoryUtils";
import type {
  MemoryCorrectionInput,
  MemoryDetail,
  MemoryFilters,
  MemorySummary,
} from "../types";

export type MemoryFilterState = Pick<MemoryFilters, "status" | "kind" | "origin">;

/**
 * State for the memory review screen.
 *
 * The API is the source of truth: the list is re-fetched with the active
 * filters after every mutation, and the detail is re-fetched so history and
 * lifecycle status cannot go stale. Requests are guarded by `isMutating` to
 * prevent duplicate submissions.
 */
export function useMemories() {
  const [filters, setFiltersState] = useState<MemoryFilterState>({});
  const [offset, setOffset] = useState(0);
  const [items, setItems] = useState<MemorySummary[]>([]);
  const [total, setTotal] = useState(0);
  const [limit, setLimit] = useState(MEMORY_FILTER_LIMIT);
  const [isLoading, setIsLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<MemoryDetail | null>(null);
  const [isDetailLoading, setIsDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailKey, setDetailKey] = useState(0);

  const [isMutating, setIsMutating] = useState(false);
  const [mutationError, setMutationError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setIsLoading(true);
    setListError(null);
    listMemories({ ...filters, limit: MEMORY_FILTER_LIMIT, offset })
      .then((response) => {
        if (cancelled) return;
        setItems(response.items);
        setTotal(response.total);
        setLimit(response.limit);
        if (response.items.length === 0 && offset > 0 && response.total > 0) {
          // A mutation removed the last item on this page: fall back to the
          // last page that still has content.
          const lastOffset =
            Math.floor((response.total - 1) / MEMORY_FILTER_LIMIT) * MEMORY_FILTER_LIMIT;
          setOffset(lastOffset);
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setListError(errorMessage(error));
        setItems([]);
        setTotal(0);
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filters, offset, reloadKey]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      setDetailError(null);
      setIsDetailLoading(false);
      return;
    }
    let cancelled = false;
    setIsDetailLoading(true);
    setDetailError(null);
    getMemory(selectedId)
      .then((memory) => {
        if (!cancelled) setDetail(memory);
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        setDetailError(errorMessage(error));
        setDetail(null);
      })
      .finally(() => {
        if (!cancelled) setIsDetailLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId, detailKey]);

  const setFilters = useCallback((next: MemoryFilterState) => {
    setFiltersState(next);
    setOffset(0);
    setSelectedId(null);
    setMutationError(null);
  }, []);

  const reloadList = useCallback(() => setReloadKey((key) => key + 1), []);

  const refreshDetail = useCallback(() => setDetailKey((key) => key + 1), []);

  const selectMemory = useCallback((memoryId: string) => {
    setSelectedId(memoryId);
    setDetailKey((key) => key + 1);
    setMutationError(null);
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedId(null);
    setMutationError(null);
  }, []);

  const runMutation = useCallback(
    async (action: () => Promise<unknown>, closeDetail = false): Promise<boolean> => {
      if (isMutating) return false;
      setIsMutating(true);
      setMutationError(null);
      try {
        await action();
        setReloadKey((key) => key + 1);
        if (closeDetail) {
          setSelectedId(null);
          setDetail(null);
        } else {
          setDetailKey((key) => key + 1);
        }
        return true;
      } catch (error) {
        setMutationError(errorMessage(error));
        // Resync so a 409 or 404 cannot leave the controls on stale status.
        setDetailKey((key) => key + 1);
        return false;
      } finally {
        setIsMutating(false);
      }
    },
    [isMutating],
  );

  const confirm = useCallback(
    (memoryId: string) => runMutation(() => confirmMemory(memoryId)),
    [runMutation],
  );

  const reject = useCallback(
    (memoryId: string, reason?: string) =>
      runMutation(() => rejectMemory(memoryId, reason)),
    [runMutation],
  );

  const remove = useCallback(
    (memoryId: string) => runMutation(() => deleteMemory(memoryId), true),
    [runMutation],
  );

  const supersede = useCallback(
    (memoryId: string, reason?: string) =>
      runMutation(() => supersedeMemory(memoryId, reason)),
    [runMutation],
  );

  const correct = useCallback(
    (memoryId: string, changes: MemoryCorrectionInput) =>
      runMutation(() => correctMemory(memoryId, changes)),
    [runMutation],
  );

  const goToPreviousPage = useCallback(() => {
    setOffset((current) => Math.max(0, current - MEMORY_FILTER_LIMIT));
  }, []);

  const goToNextPage = useCallback(() => {
    setOffset((current) =>
      current + MEMORY_FILTER_LIMIT < total ? current + MEMORY_FILTER_LIMIT : current,
    );
  }, [total]);

  const clearMutationError = useCallback(() => setMutationError(null), []);

  return {
    filters,
    setFilters,
    items,
    total,
    limit,
    offset,
    isLoading,
    listError,
    reloadList,
    goToPreviousPage,
    goToNextPage,
    selectedId,
    detail,
    isDetailLoading,
    detailError,
    selectMemory,
    clearSelection,
    refreshDetail,
    isMutating,
    mutationError,
    clearMutationError,
    confirm,
    reject,
    remove,
    supersede,
    correct,
  };
}
