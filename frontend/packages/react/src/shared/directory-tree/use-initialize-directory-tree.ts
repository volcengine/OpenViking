import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import type { Dispatch, RefObject, SetStateAction } from 'react';
import type { DirectoryQueries } from './directory-queries.js';
import { expandChain } from './fs-path.js';
import {
  restoreDirectoryExpansion,
  type UnresolvedExpansion,
} from './restore-expansion.js';

const emptyExpansion: UnresolvedExpansion = new Map();

interface RestoreAttempt {
  session: DirectoryQueries;
  signal: AbortSignal;
  retryQueued: boolean;
  unresolved: UnresolvedExpansion;
}

export function useInitializeDirectoryTree({
  session,
  rootUri,
  initialPath,
  savedExpansion,
  expandedRef,
  setExpanded,
}: {
  session?: DirectoryQueries;
  rootUri: string;
  initialPath?: string;
  savedExpansion?: ReadonlySet<string>;
  expandedRef: RefObject<ReadonlySet<string>>;
  setExpanded: Dispatch<SetStateAction<ReadonlySet<string>>>;
}) {
  const [initialized, setInitialized] = useState<{
    session: DirectoryQueries | undefined;
    uri?: string;
    attempt: number;
    unresolved: UnresolvedExpansion;
  }>();
  const [attempt, restart] = useReducer((count: number) => count + 1, 0);
  const initialLoad = useRef<Promise<void> | undefined>(undefined);
  const activeAttempt = useRef<RestoreAttempt | undefined>(undefined);
  const retryExpansion = useCallback(() => {
    const current = activeAttempt.current;
    if (
      !savedExpansion ||
      !current ||
      current.session !== session ||
      current.signal.aborted ||
      current.retryQueued
    )
      return;
    current.retryQueued = true;
    // Let the current pass finish without cancelling a sibling listing.
    void initialLoad.current?.then(() => {
      if (activeAttempt.current !== current || current.signal.aborted) return;
      if (current.unresolved.size) restart();
      else current.retryQueued = false;
    });
  }, [session, savedExpansion]);
  // Synchronize the initial/default path with directory subscriptions after commit.
  // Navigation and identity can change without a click. Cleanup only invalidates
  // this navigation; Query observers own shared request cancellation.
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    const saved = [...(savedExpansion ?? [])].filter((uri) => uri !== rootUri);
    const previous = activeAttempt.current;
    const cached =
      previous?.session === session && previous.retryQueued
        ? session.dirs.current
        : undefined;
    const load: DirectoryQueries['load'] = async (uri, offset, signal) => {
      const page = cached?.[uri];
      if (offset === 0 && page?.ready && !page.loading && !page.error)
        return page;
      return session.load(uri, offset, signal);
    };
    const current: RestoreAttempt = {
      session,
      signal: controller.signal,
      retryQueued: false,
      unresolved: saved.length ? new Map([[rootUri, saved]]) : emptyExpansion,
    };
    activeAttempt.current = current;
    initialLoad.current = (async () => {
      const page = await load(rootUri, 0, controller.signal);
      if (savedExpansion) {
        current.unresolved = await restoreDirectoryExpansion(
          load,
          rootUri,
          page,
          savedExpansion,
          (uri) => expandedRef.current.has(uri),
          controller.signal,
          (saved, listed) => {
            if (saved === listed) return;
            setExpanded((previous) => {
              if (!previous.has(saved)) return previous;
              const next = new Set(previous);
              next.delete(saved);
              next.add(listed);
              return next;
            });
          },
        );
        return;
      }
      for (const uri of expandChain(rootUri, initialPath)) {
        if (controller.signal.aborted) return;
        const page = await session.load(uri, 0, controller.signal);
        if (page?.error) break;
        if (!controller.signal.aborted)
          setExpanded((previous) => new Set(previous).add(uri));
      }
    })()
      .catch(() => undefined)
      .finally(() => {
        if (!controller.signal.aborted)
          setInitialized({
            session,
            uri: initialPath,
            attempt,
            unresolved: current.unresolved,
          });
      });
    return () => controller.abort();
  }, [
    session,
    rootUri,
    initialPath,
    savedExpansion,
    expandedRef,
    setExpanded,
    attempt,
  ]);
  const initializing =
    !session ||
    initialized?.session !== session ||
    initialized.uri !== initialPath ||
    initialized.attempt !== attempt;
  return {
    initialLoad,
    initializing,
    unresolved: initializing ? emptyExpansion : initialized.unresolved,
    retryExpansion,
  };
}
