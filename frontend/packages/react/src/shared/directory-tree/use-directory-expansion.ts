import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { RefObject, SetStateAction } from 'react';
import { normalizeResourceUri, resourceUriIdentity } from '../resource-uri.js';
import type { QueryRequest } from '../request/use-query-scope.js';
import { useResourceUriIdentity } from '../request/use-resource-uri-identity.js';
import type { DirectoryQueries } from './directory-queries.js';
import { expansionPath } from './expansion-path.js';
import type { DirectoryExpansionSnapshot } from './types.js';

interface ExpansionState {
  expanded: ReadonlySet<string>;
  collapsed: ReadonlySet<string>;
  edited: boolean;
}

function matchesIdentity(uri: string, home?: string) {
  const key = resourceUriIdentity(uri, home);
  return (candidate: string) => resourceUriIdentity(candidate, home) === key;
}

function underCollapse(uri: string, collapsed: ReadonlySet<string>) {
  return [...collapsed].some(
    (fold) => uri === fold || uri.startsWith(`${fold}/`),
  );
}

export function useDirectoryExpansion({
  session,
  activeSession,
  request,
  rootUri,
  cacheScopeKey,
  defaultExpandedUris,
  defaultExpansionSnapshot,
}: {
  session: DirectoryQueries | undefined;
  activeSession: RefObject<DirectoryQueries | undefined>;
  request: QueryRequest | undefined;
  rootUri: string;
  cacheScopeKey?: string;
  defaultExpandedUris?: readonly string[];
  defaultExpansionSnapshot?: DirectoryExpansionSnapshot;
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
  const [legacySnapshot] = useState(defaultExpandedUris);
  const [structuredSnapshot] = useState(defaultExpansionSnapshot);
  const seedSession = useRef<DirectoryQueries>(undefined);
  // Bind the host's mount snapshot to the first committed query scope. A later
  // root or identity can use the same alias for a different directory.
  useEffect(() => {
    if (session) seedSession.current ??= session;
  }, [session]);
  const seed =
    !seedSession.current || seedSession.current === session
      ? structuredSnapshot
      : undefined;
  const savedExpansion = seed?.expandedUris ?? legacySnapshot;
  const savedCollapses = seed?.collapsedUris;
  const initial = useMemo<ExpansionState>(() => {
    const inRoot = (uris: readonly string[] = []) =>
      uris
        .map(normalizeResourceUri)
        .filter((uri) => expansionPath(rootUri, uri).length);
    return {
      expanded: new Set([rootUri, ...inRoot(savedExpansion)]),
      collapsed: new Set(inRoot(savedCollapses)),
      edited: false,
    };
  }, [rootUri, savedExpansion, savedCollapses]);
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
      const home = readHome();
      const matches = matchesIdentity(uri, home);
      const { collapsed, expanded } = current.current;
      return (
        (expanded.has(uri) || [...expanded].some(matches)) &&
        !underCollapse(
          resourceUriIdentity(uri, home),
          new Set(
            [...collapsed].map((fold) => resourceUriIdentity(fold, home)),
          ),
        )
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
      [...state.expanded].filter(
        (uri) => !underCollapse(identifyUri(uri), collapsed),
      ),
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
