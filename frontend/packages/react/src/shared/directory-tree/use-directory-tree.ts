import { tagData } from '../monitoring/data.js';
import { useDirectoryMonitoring } from './use-directory-monitoring.js';
import {
  useCallback,
  useLayoutEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
} from 'react';
import { useRequestScope } from '../request/use-request-scope.js';
import { expandChain } from './fs-path.js';
import { useInitializeDirectoryTree } from './use-initialize-directory-tree.js';
import { useReportExpansion } from './use-report-expansion.js';
import { useDirectoryExpansion } from './use-directory-expansion.js';
import { useDirectoryRows } from './use-directory-rows.js';
import { DirectoryQueries } from './directory-queries.js';
import { useQueryScope } from '../request/use-query-scope.js';
import type { DirState, UseDirectoryTreeOptions } from './types.js';
import { useUserRootMode } from './user-root-context.js';

const empty: Record<string, DirState> = {};
const subscribeEmpty = () => () => {};
const emptySnapshot = () => empty;
const emptyDirectoryRef = { current: empty };
const DIRECTORY_LOAD = 'file_browser_directory_load';

export function useDirectoryTree({
  client,
  rootUri,
  hideDeniedEntries,
  cacheScopeKey,
  defaultExpandedUri,
  defaultExpandedUris,
  defaultExpansionSnapshot,
  onExpandedUrisChange,
  onExpansionSnapshotChange,
  requestOptions,
}: UseDirectoryTreeOptions) {
  const userRootMode = useUserRootMode();
  const {
    request,
    aborted,
    error: identityError,
    retry: retryScope,
  } = useQueryScope(client, requestOptions, DIRECTORY_LOAD, cacheScopeKey);
  const session = useMemo(
    () =>
      request &&
      new DirectoryQueries(request, rootUri, cacheScopeKey, userRootMode),
    [request, rootUri, cacheScopeKey, userRootMode],
  );
  const dirs = useSyncExternalStore(
    aborted ? subscribeEmpty : (session?.subscribe ?? subscribeEmpty),
    session?.getSnapshot ?? emptySnapshot,
  );
  const activeSession = useRef(session);
  // Publish the committed query scope before event or restore callbacks run.
  // Layout publication ignores suspended renders. Each commit replaces the ref.
  useLayoutEffect(() => {
    activeSession.current = session;
  }, [session]);
  const expansion = useDirectoryExpansion({
    session,
    activeSession,
    request,
    rootUri,
    cacheScopeKey,
    defaultExpandedUris,
    defaultExpansionSnapshot,
  });
  const { expanded, isExpanded, edit } = expansion;
  const {
    initialLoad,
    initializing,
    unresolved,
    retryDirectory,
    retryExpansion,
  } = useInitializeDirectoryTree({
    session,
    rootUri,
    initialPath: expansion.savedExpansion ? undefined : defaultExpandedUri,
    expansion,
  });
  const scope = useRequestScope(
    request?.client ?? null,
    request?.options,
    rootUri,
    userRootMode,
  );
  const fetchPage = useCallback(
    async (...args: Parameters<DirectoryQueries['load']>) =>
      activeSession.current?.load(...args).catch(() => undefined),
    [],
  );
  const expandForNavigation = useCallback(
    (uri: string) => {
      if (activeSession.current !== session || isExpanded(uri)) return;
      edit(uri, true);
    },
    [session, isExpanded, edit],
  );
  const state = useMemo(
    () => ({
      open: async (uri: string) => {
        edit(uri, true);
        await fetchPage(uri, 0);
      },
      openPath: async (uri: string) => {
        const current = scope.current();
        for (const parent of expandChain(rootUri, uri)) {
          if (scope.current() !== current || current.controller.signal.aborted)
            return;
          edit(parent, true);
          await fetchPage(parent, 0);
        }
      },
      toggleExpand: async (row: { uri: string; disabled?: boolean }) => {
        if (row.disabled) return;
        const isOpen = isExpanded(row.uri);
        edit(row.uri, !isOpen);
        if (!isOpen) await fetchPage(row.uri, 0);
      },
      loadMore: async (uri: string) => {
        await fetchPage(uri, 1);
      },
      retry: async (uri: string) =>
        activeSession.current ? retryDirectory(uri) : retryScope(),
      refresh: async () => {
        const current = activeSession.current;
        if (!current) return retryScope();
        await current.refresh();
        if (activeSession.current === current) retryExpansion();
      },
    }),
    [
      scope,
      rootUri,
      isExpanded,
      edit,
      fetchPage,
      retryScope,
      retryDirectory,
      retryExpansion,
    ],
  );
  const { rows, reconciling } = useDirectoryRows({
    session,
    rootUri,
    dirs,
    hideDeniedEntries,
    expanded,
    collapsed: expansion.collapsed,
    pendingEdits: expansion.pendingEdits,
    identifyUri: expansion.identifyUri,
    resolveHome: expansion.resolveHome,
    initializing,
  });
  const monitoring = useDirectoryMonitoring(
    dirs,
    dirs[rootUri]?.ready ?? false,
  );
  const spans = monitoring.map((operation) => operation.span);
  tagData(spans[0]?.binding, rows, spans);
  const isInitializing = !identityError && initializing;
  const root = dirs[rootUri];
  const isReady = root?.ready ?? false;
  useReportExpansion(
    session,
    isReady &&
      (!isInitializing || expansion.edited) &&
      (!root?.error || (expansion.edited && root.entries.length > 0)) &&
      (!unresolved.size || expansion.edited),
    rootUri,
    rows,
    unresolved,
    expansion,
    reconciling,
    onExpandedUrisChange,
    onExpansionSnapshotChange,
  );
  return {
    monitoring,
    rows,
    isInitializing,
    isReady,
    error: identityError ?? dirs[rootUri]?.error ?? null,
    ...state,
    dirs,
    navigationInputs: {
      scope,
      dirs: session?.dirs ?? emptyDirectoryRef,
      initialLoad,
      isExpanded,
      identifyUri: expansion.identifyUri,
      resolveHome: expansion.resolveHome,
      fetchPage,
      expandForNavigation,
    },
  };
}
