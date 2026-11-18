import { useCallback, useMemo, useRef, useState } from 'react';
import type { RefObject, SetStateAction } from 'react';
import { normalizeResourceUri, resourceUriIdentity } from '../resource-uri.js';
import type { QueryRequest } from '../request/use-query-scope.js';
import { useResourceUriIdentity } from '../request/use-resource-uri-identity.js';
import type { DirectoryQueries } from './directory-queries.js';
import { expansionPath } from './expansion-path.js';

interface ExpansionState {
  expanded: ReadonlySet<string>;
  collapsed: ReadonlySet<string>;
  edited: boolean;
}

function matchesIdentity(uri: string, home?: string) {
  const key = resourceUriIdentity(uri, home);
  return (candidate: string) => resourceUriIdentity(candidate, home) === key;
}

export function useDirectoryExpansion({
  session,
  activeSession,
  request,
  rootUri,
  cacheScopeKey,
  defaultExpandedUris,
}: {
  session: DirectoryQueries | undefined;
  activeSession: RefObject<DirectoryQueries | undefined>;
  request: QueryRequest | undefined;
  rootUri: string;
  cacheScopeKey?: string;
  defaultExpandedUris?: readonly string[];
}) {
  const { identifyUri, readHome, resolveHome } = useResourceUriIdentity(
    request?.client ?? null,
    request?.options,
    rootUri,
    cacheScopeKey,
    request?.signal,
  );
  const [local, setLocal] = useState<{
    session: DirectoryQueries | undefined;
    state: ExpansionState;
  }>();
  const [savedExpansion] = useState(defaultExpandedUris);
  const initial = useMemo<ExpansionState>(
    () => ({
      expanded: new Set([
        rootUri,
        ...(savedExpansion ?? [])
          .map(normalizeResourceUri)
          .filter((uri) => expansionPath(rootUri, uri).length > 0),
      ]),
      collapsed: new Set(),
      edited: false,
    }),
    [rootUri, savedExpansion],
  );
  const state = local && local.session === session ? local.state : initial;
  const current = useRef(state);
  current.current = state;

  const update = useCallback(
    (change: (previous: ExpansionState) => ExpansionState) => {
      setLocal((previous) => {
        if (activeSession.current !== session) return previous;
        const before =
          previous && previous.session === session ? previous.state : initial;
        const next = change(before);
        if (next === before) return previous;
        current.current = next;
        return { session, state: next };
      });
    },
    [activeSession, initial, session],
  );
  const setExpanded = useCallback(
    (change: SetStateAction<ReadonlySet<string>>) =>
      update((previous) => ({
        ...previous,
        expanded:
          typeof change === 'function' ? change(previous.expanded) : change,
      })),
    [update],
  );
  const isExpanded = useCallback(
    (uri: string) => {
      const matches = matchesIdentity(uri, readHome());
      const { collapsed, expanded } = current.current;
      return (
        ![...collapsed].some(matches) &&
        (expanded.has(uri) || [...expanded].some(matches))
      );
    },
    [readHome],
  );
  const edit = useCallback(
    (uri: string, open: boolean) =>
      update((previous) => {
        const matches = matchesIdentity(uri, readHome());
        const other = (candidate: string) => !matches(candidate);
        if (open) {
          return {
            edited: true,
            expanded: new Set([...previous.expanded].filter(other)).add(uri),
            collapsed: new Set([...previous.collapsed].filter(other)),
          };
        }
        return {
          edited: true,
          expanded: new Set([...previous.expanded].filter(other)),
          collapsed: new Set(previous.collapsed).add(uri),
        };
      }),
    [readHome, update],
  );
  const restoreListed = useCallback(
    (uri: string) =>
      update((previous) => {
        const matches = matchesIdentity(uri, readHome());
        if (
          previous.expanded.has(uri) ||
          [...previous.collapsed].some(matches) ||
          ![...previous.expanded].some(matches)
        )
          return previous;
        const expanded = new Set(
          [...previous.expanded].filter((candidate) => !matches(candidate)),
        ).add(uri);
        return { ...previous, expanded };
      }),
    [readHome, update],
  );
  const expanded = useMemo(() => {
    if (!state.collapsed.size) return state.expanded;
    const collapsed = new Set([...state.collapsed].map(identifyUri));
    return new Set(
      [...state.expanded].filter((uri) => !collapsed.has(identifyUri(uri))),
    );
  }, [state.expanded, state.collapsed, identifyUri]);
  return {
    expanded,
    collapsed: state.collapsed,
    edited: state.edited,
    savedExpansion: savedExpansion === undefined ? undefined : initial.expanded,
    identifyUri,
    readHome,
    resolveHome,
    isExpanded,
    setExpanded,
    restoreListed,
    edit,
  };
}
