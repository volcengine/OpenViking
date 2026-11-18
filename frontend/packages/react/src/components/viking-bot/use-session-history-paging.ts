import { useCallback, useEffect, useMemo, useState } from 'react';
import type { VikingBotSessions } from './capabilities.js';

export function useSessionHistoryPaging(
  open: boolean,
  sessions: VikingBotSessions,
) {
  const {
    scopeKey,
    currentId,
    items,
    hasMore,
    isLoading,
    error,
    onLoadMore,
    onLoadMoreWithSignal,
  } = sessions;
  const canLoad = Boolean(hasMore && (onLoadMoreWithSignal || onLoadMore));
  const owner = useMemo(
    () => ({
      open,
      scopeKey,
      currentId,
      requested: false,
      seenIds: new Set<string>(),
      request: undefined as AbortController | undefined,
    }),
    [open, scopeKey, currentId],
  );
  const [status, setStatus] = useState<{
    owner: typeof owner;
    loading?: boolean;
    error?: string;
  }>();
  const current = status?.owner === owner ? status : undefined;

  // Effect necessity: invalidate late page results and abort opt-in requests
  // when the popup/account/session loses ownership. A load handler cannot
  // observe those transitions. Free the request slot for Strict Mode replay.
  useEffect(
    () => () => {
      owner.request?.abort();
      owner.request = undefined;
    },
    [owner],
  );

  const requestPage = useCallback(
    (visibleIds?: readonly string[]) => {
      if (!open || !canLoad || isLoading || owner.request) return;
      const controller = new AbortController();
      owner.request = controller;
      const { signal } = controller;
      void (async () => {
        // A Strict Mode setup may be immediately cleaned up before notifying the host.
        await Promise.resolve();
        if (signal.aborted) return;
        if (visibleIds) {
          owner.requested = true;
          visibleIds.forEach((id) => owner.seenIds.add(id));
        }
        setStatus({ owner, loading: true });
        try {
          await Promise.resolve(
            onLoadMoreWithSignal
              ? onLoadMoreWithSignal(signal)
              : onLoadMore?.(),
          );
          if (!signal.aborted) setStatus({ owner });
        } catch (cause) {
          if (!signal.aborted)
            setStatus({
              owner,
              error: cause instanceof Error ? cause.message : String(cause),
            });
        } finally {
          if (owner.request === controller) owner.request = undefined;
        }
      })();
    },
    [open, canLoad, isLoading, onLoadMore, onLoadMoreWithSignal, owner],
  );

  const visibleIds = useMemo(() => {
    if (!open || !currentId || !canLoad) return;
    if (!items.some(({ id }) => id === currentId))
      return items.map(({ id }) => id);
  }, [open, currentId, canLoad, items]);

  // Effect necessity: the host can publish each page after the opening event.
  // Seek again only when previously unseen sessions appear, and stop on
  // errors. The owner effect invalidates the request after scope changes.
  useEffect(() => {
    if (!visibleIds || current?.loading || current?.error || error) return;
    if (!owner.requested || visibleIds.some((id) => !owner.seenIds.has(id)))
      requestPage(visibleIds);
  }, [visibleIds, current, error, owner, requestPage]);

  return {
    canLoad,
    loading: current?.loading,
    error: current?.error,
    loadMore: () => requestPage(visibleIds),
  };
}
