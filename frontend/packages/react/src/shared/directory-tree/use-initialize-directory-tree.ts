import { useEffect, useRef, useState } from 'react';
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
  }>();
  const initialLoad = useRef<Promise<void> | undefined>(undefined);
  // Synchronize the initial/default path with directory subscriptions after commit.
  // Navigation and identity can change without a click. Cleanup only invalidates
  // this navigation; Query observers own shared request cancellation.
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    initialLoad.current = (async () => {
      const page = await session.load(rootUri, 0, controller.signal);
      if (savedExpansion) {
        await restoreDirectoryExpansion(
          session.load,
          rootUri,
          page,
          savedExpansion,
          (uri) => expandedRef.current.has(uri),
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
          setInitialized({ session, uri: initialPath });
      });
    return () => controller.abort();
  }, [session, rootUri, initialPath, savedExpansion, expandedRef, setExpanded]);
  return {
    initialLoad,
    initializing:
      !session ||
      initialized?.session !== session ||
      initialized?.uri !== initialPath,
  };
}
