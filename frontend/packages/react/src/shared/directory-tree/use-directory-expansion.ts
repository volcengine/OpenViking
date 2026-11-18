import { useMemo, useRef, useState } from 'react';
import type { RefObject, SetStateAction } from 'react';
import { normalizeResourceUri } from '../resource-uri.js';
import type { DirectoryQueries } from './directory-queries.js';
import { expansionPath } from './expansion-path.js';

export function useDirectoryExpansion(
  session: DirectoryQueries | undefined,
  activeSession: RefObject<DirectoryQueries | undefined>,
  rootUri: string,
  defaultExpandedUris: readonly string[] | undefined,
) {
  const [local, setLocal] = useState<{
    session: DirectoryQueries | undefined;
    expanded: ReadonlySet<string>;
  }>();
  const [savedExpansion] = useState(defaultExpandedUris);
  const initialExpanded = useMemo(
    () =>
      new Set([
        rootUri,
        ...(savedExpansion ?? [])
          .map(normalizeResourceUri)
          .filter((uri) => expansionPath(rootUri, uri).length > 0),
      ]),
    [rootUri, savedExpansion],
  );
  const expanded =
    local && local.session === session ? local.expanded : initialExpanded;
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;
  const setExpanded = useMemo(
    () => (update: SetStateAction<ReadonlySet<string>>) => {
      setLocal((previous) => {
        const session = activeSession.current;
        const current =
          previous && previous.session === session
            ? previous.expanded
            : initialExpanded;
        const next = typeof update === 'function' ? update(current) : update;
        expandedRef.current = next;
        return { session, expanded: next };
      });
    },
    [activeSession, initialExpanded],
  );
  return {
    expanded,
    expandedRef,
    initialExpanded,
    hasSavedExpansion: savedExpansion !== undefined,
    setExpanded,
  };
}
