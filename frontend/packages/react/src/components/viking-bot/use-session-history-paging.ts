import { useCallback, useEffect, useMemo, useState } from 'react';
import type { VikingBotSessions } from './capabilities.js';

export function useSessionHistoryPaging(
  open: boolean,
  sessions: VikingBotSessions,
) {
  const { scopeKey, currentId, items, hasMore, isLoading, error, onLoadMore } =
    sessions;
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

  // Effect necessity: abort the host's page request when this popup/account/
  // session stops owning it, including when outside dismissal unmounts the UI.
  // A load handler does not observe those transitions. Release the pending
  // slot too, so Strict Mode can set up the same owner again after cleanup.
  useEffect(
    () => () => {
      owner.request?.abort();
      owner.request = undefined;
    },
    [owner],
  );

  const requestPage = useCallback(
    (visibleIds?: readonly string[]) => {
      if (!open || !hasMore || isLoading || !onLoadMore || owner.request)
        return;
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
          await onLoadMore(signal);
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
    [open, hasMore, isLoading, onLoadMore, owner],
  );

  const visibleIds = useMemo(() => {
    if (!open || !currentId || !hasMore || !onLoadMore) return;
    if (!items.some(({ id }) => id === currentId))
      return items.map(({ id }) => id);
  }, [open, currentId, hasMore, items, onLoadMore]);

  // Effect necessity: the host can publish each page after the opening event.
  // Seek again only when previously unseen sessions appear, and stop on
  // errors. The owner effect aborts any request after scope changes.
  useEffect(() => {
    if (!visibleIds || current?.loading || current?.error || error) return;
    if (!owner.requested || visibleIds.some((id) => !owner.seenIds.has(id)))
      requestPage(visibleIds);
  }, [visibleIds, current, error, owner, requestPage]);

  return {
    loading: current?.loading,
    error: current?.error,
    loadMore: () => requestPage(visibleIds),
  };
}
