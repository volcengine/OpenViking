import { useEffect, useReducer, useRef, useState } from 'react';
import type { Dispatch, RefObject, SetStateAction } from 'react';
import type { DirectoryQueries } from './directory-queries.js';
import { expandChain } from './fs-path.js';
import { restoreDirectoryExpansion } from './restore-expansion.js';

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
    expansionComplete: boolean;
  }>();
  const [attempt, retryExpansion] = useReducer((count: number) => count + 1, 0);
  const initialLoad = useRef<Promise<void> | undefined>(undefined);
  // Synchronize the initial/default path with directory subscriptions after commit.
  // Navigation and identity can change without a click. Cleanup only invalidates
  // this navigation; Query observers own shared request cancellation.
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    let expansionComplete = savedExpansion === undefined;
    initialLoad.current = (async () => {
      const page = await session.load(rootUri, 0, controller.signal);
      if (savedExpansion) {
        expansionComplete = await restoreDirectoryExpansion(
          session.load,
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
            expansionComplete,
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
  const expansionComplete = !initializing && initialized.expansionComplete;
  return {
    initialLoad,
    initializing,
    expansionComplete,
    retryExpansion:
      !initializing && !expansionComplete ? retryExpansion : undefined,
  };
}
