import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { RefObject, SetStateAction } from 'react';
import {
  HOME_URI,
  normalizeResourceUri,
  resourceUriIdentity,
} from '../resource-uri.js';
import type { QueryRequest } from '../request/use-query-scope.js';
import { useResourceUriIdentity } from '../request/use-resource-uri-identity.js';
import type { DirectoryQueries } from './directory-queries.js';
import { expansionPath } from './expansion-path.js';
import type { DirectoryExpansionSnapshot } from './types.js';

import {
  type ExpansionState,
  applyExpansionEdit,
  matchesIdentity,
  settleExpansionEdits,
  underCollapse,
  unresolvedFold,
} from './expansion-edits.js';

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
  const savedEdits = seed?.pendingEdits;
  const initial = useMemo<ExpansionState>(() => {
    const inRoot = (uris: readonly string[] = []) =>
      uris
        .map(normalizeResourceUri)
        .filter((uri) => expansionPath(rootUri, uri).length);
    return {
      expanded: new Set([rootUri, ...inRoot(savedExpansion)]),
      collapsed: new Set(inRoot(savedCollapses)),
      pendingEdits: (savedEdits ?? []).filter(
        ({ uri }) => expansionPath(rootUri, uri).length,
      ),
      edited: false,
    };
  }, [rootUri, savedExpansion, savedCollapses, savedEdits]);
  const raw = local && local.session === session ? local.state : initial;
  const identifiedHome = identifyUri(HOME_URI);
  const home = identifiedHome === HOME_URI ? undefined : identifiedHome;
  const state = useMemo(() => settleExpansionEdits(raw, home), [raw, home]);
  const current = useRef(state);
  // Event handlers and async restore callbacks read the committed expansion.
  // A suspended render must not publish another scope or pending state.
  // Each commit replaces the ref; there is no external subscription to dispose.
  useLayoutEffect(() => {
    current.current = state;
  }, [state]);

  const update = useCallback(
    (change: (previous: ExpansionState) => ExpansionState) => {
      setLocal((previous) => {
        if (activeSession.current !== session) return previous;
        const before =
          previous && previous.session === session ? previous.state : initial;
        const next = change(settleExpansionEdits(before, readHome()));
        if (next === before) return previous;
        return { session, state: next };
      });
    },
    [activeSession, initial, readHome, session],
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
      const { collapsed, expanded } = settleExpansionEdits(
        current.current,
        home,
      );
      return (
        (expanded.has(uri) || [...expanded].some(matches)) &&
        (Boolean(home) || !unresolvedFold(current.current, uri)) &&
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
      update((previous) =>
        applyExpansionEdit(previous, { uri, expanded: open }, readHome()),
      ),
    [readHome, update],
  );
  const isDeferred = useCallback(
    (uri: string) => !readHome() && unresolvedFold(current.current, uri),
    [readHome],
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
        (uri) =>
          !underCollapse(identifyUri(uri), collapsed) &&
          (Boolean(home) || !unresolvedFold(state, uri)),
      ),
    );
  }, [state, home, identifyUri]);
  return {
    expanded,
    collapsed: state.collapsed,
    pendingEdits: state.pendingEdits,
    edited: state.edited,
    savedExpansion: savedExpansion === undefined ? undefined : initial.expanded,
    identifyUri,
    readHome,
    resolveHome,
    isExpanded,
    isDeferred,
    setExpanded,
    restoreListed,
    edit,
  };
}
