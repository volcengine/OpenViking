import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react';
import type { useDirectoryExpansion } from './use-directory-expansion.js';
import type { DirectoryQueries } from './directory-queries.js';
import { HOME_URI } from '../resource-uri.js';
import { expandChain } from './fs-path.js';
import {
  restoreDirectoryExpansion,
  type UnresolvedExpansion,
} from './restore-expansion.js';

const emptyExpansion: UnresolvedExpansion = new Map();

interface RestoreAttempt {
  session: DirectoryQueries;
  home?: string;
  signal: AbortSignal;
  retryQueued: boolean;
  listedUris: Set<string>;
  unresolved: UnresolvedExpansion;
}

export function useInitializeDirectoryTree({
  session,
  rootUri,
  initialPath,
  expansion,
}: {
  session?: DirectoryQueries;
  rootUri: string;
  initialPath?: string;
  expansion: ReturnType<typeof useDirectoryExpansion>;
}) {
  const {
    savedExpansion,
    identifyUri,
    isExpanded,
    isDeferred,
    readHome,
    resolveHome,
    setExpanded,
    restoreListed,
  } = expansion;
  const pendingExpansion = useMemo<UnresolvedExpansion>(() => {
    const saved = [...(savedExpansion ?? [])].filter((uri) => uri !== rootUri);
    return saved.length ? new Map([[rootUri, saved]]) : emptyExpansion;
  }, [rootUri, savedExpansion]);
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
      // Identity can settle after grouping, making an exhausted group stale.
      if (current.unresolved.size || current.home !== readHome()) restart();
      else current.retryQueued = false;
    });
  }, [session, savedExpansion, readHome]);
  const homeUri = identifyUri(HOME_URI);
  const previousHome = useRef<{
    session: DirectoryQueries | undefined;
    uri: string;
  }>(undefined);
  // Resume server listings when a verified home identity releases deferred
  // folders; the lookup can finish without a user event. The retry queue waits
  // for the active pass and ignores replaced sessions or aborted attempts.
  useEffect(() => {
    const previous = previousHome.current;
    previousHome.current = { session, uri: homeUri };
    if (
      previous &&
      previous.session === session &&
      homeUri !== HOME_URI &&
      previous.uri !== homeUri
    )
      retryExpansion();
  }, [session, homeUri, retryExpansion]);
  const retryDirectory = useCallback(
    async (uri: string) => {
      const current = activeAttempt.current;
      if (!current || current.session !== session || current.signal.aborted)
        return;
      if (current.listedUris.has(uri)) {
        // Keep the failed row visible until the active pass can be retried.
        await initialLoad.current;
        if (activeAttempt.current !== current || current.signal.aborted) return;
      }
      const page = await current.session
        .load(uri, 0, undefined, true)
        .catch(() => undefined);
      if (page && !page.error && activeAttempt.current === current)
        retryExpansion();
    },
    [session, retryExpansion],
  );
  // Synchronize the initial/default path with directory subscriptions after commit.
  // Navigation and identity can change without a click. Cleanup only invalidates
  // this navigation; Query observers own shared request cancellation.
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    const previous = activeAttempt.current;
    const cached =
      previous?.session === session && previous.retryQueued
        ? session.dirs.current
        : undefined;
    const current: RestoreAttempt = {
      session,
      home: readHome(),
      signal: controller.signal,
      retryQueued: false,
      listedUris: new Set(),
      unresolved: pendingExpansion,
    };
    const load: DirectoryQueries['load'] = async (uri, offset, signal) => {
      if (savedExpansion) current.listedUris.add(uri);
      const page = cached?.[uri];
      if (offset === 0 && page?.ready && !page.loading && !page.error)
        return page;
      return session.load(uri, offset, signal);
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
          { isExpanded, isDeferred, readHome, resolveHome, restoreListed },
          controller.signal,
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
    pendingExpansion,
    savedExpansion,
    isExpanded,
    isDeferred,
    readHome,
    resolveHome,
    setExpanded,
    restoreListed,
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
    unresolved: initializing ? pendingExpansion : initialized.unresolved,
    retryDirectory,
    retryExpansion,
  };
}
