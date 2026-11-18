import { tagData } from '../monitoring/data.js';
import { useDirectoryMonitoring } from './use-directory-monitoring.js';
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useSyncExternalStore,
} from 'react';
import { useRequestScope } from '../request/use-request-scope.js';
import { expandChain } from './fs-path.js';
import { useInitializeDirectoryTree } from './use-initialize-directory-tree.js';
import { useReportExpansion } from './use-report-expansion.js';
import { useDirectoryExpansion } from './use-directory-expansion.js';
import { flattenTree } from './rows.js';
import { DirectoryQueries } from './directory-queries.js';
import { useQueryScope } from '../request/use-query-scope.js';
import type { DirState, UseDirectoryTreeOptions } from './types.js';

const empty: Record<string, DirState> = {};
const subscribeEmpty = () => () => {};
const emptySnapshot = () => empty;
const emptyDirectoryRef = { current: empty };
const DIRECTORY_LOAD = 'file_browser_directory_load';

export function useDirectoryTree({
  client,
  rootUri,
  cacheScopeKey,
  defaultExpandedUri,
  defaultExpandedUris,
  onExpandedUrisChange,
  requestOptions,
}: UseDirectoryTreeOptions) {
  const {
    request,
    aborted,
    error: identityError,
    retry: retryScope,
  } = useQueryScope(client, requestOptions, DIRECTORY_LOAD, cacheScopeKey);
  const session = useMemo(
    () => request && new DirectoryQueries(request, rootUri, cacheScopeKey),
    [request, rootUri, cacheScopeKey],
  );
  const dirs = useSyncExternalStore(
    aborted ? subscribeEmpty : (session?.subscribe ?? subscribeEmpty),
    session?.getSnapshot ?? emptySnapshot,
  );
  const activeSession = useRef(session);
  // Publish committed query subscriptions for stable event callbacks. Abandoned
  // renders must not replace the session used by the visible preview.
  useEffect(() => {
    activeSession.current = session;
  }, [session]);
  const expansion = useDirectoryExpansion({
    session,
    activeSession,
    request,
    rootUri,
    cacheScopeKey,
    defaultExpandedUris,
  });
  const { expanded, hasListedExpansion, edit, setExpanded } = expansion;
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
  );
  const fetchPage = useCallback(
    async (...args: Parameters<DirectoryQueries['load']>) => {
      try {
        return await activeSession.current?.load(...args);
      } catch {
        return undefined;
      }
    },
    [],
  );
  const expandForNavigation = useCallback(
    (uri: string) => {
      if (activeSession.current !== session || hasListedExpansion(uri)) return;
      edit(uri, true);
    },
    [session, hasListedExpansion, edit],
  );
  const state = useMemo(
    () => ({
      open: async (uri: string) => {
        setExpanded((previous) => new Set(previous).add(uri));
        await fetchPage(uri, 0);
      },
      openPath: async (uri: string) => {
        const current = scope.current();
        for (const parent of expandChain(rootUri, uri)) {
          if (scope.current() !== current || current.controller.signal.aborted)
            return;
          setExpanded((previous) => new Set(previous).add(parent));
          await fetchPage(parent, 0);
        }
      },
      toggleExpand: async (row: { uri: string; disabled?: boolean }) => {
        if (row.disabled) return;
        const isOpen = hasListedExpansion(row.uri);
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
      hasListedExpansion,
      edit,
      setExpanded,
      fetchPage,
      retryScope,
      retryDirectory,
      retryExpansion,
    ],
  );
  const rows = useMemo(
    () => flattenTree(rootUri, 0, dirs, expanded),
    [rootUri, dirs, expanded],
  );
  const monitoring = useDirectoryMonitoring(
    dirs,
    dirs[rootUri]?.ready ?? false,
  );
  tagData(
    monitoring[0]?.span.binding,
    rows,
    monitoring.map((operation) => operation.span),
  );
  const isInitializing = !identityError && initializing;
  const root = dirs[rootUri];
  const isReady = root?.ready ?? false;
  useReportExpansion(
    session,
    isReady &&
      !isInitializing &&
      (!root?.error || (expansion.edited && root.entries.length > 0)) &&
      (!unresolved.size || expansion.edited),
    rootUri,
    rows,
    unresolved,
    expansion,
    onExpandedUrisChange,
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
      isExpanded: expansion.isExpanded,
      identifyUri: expansion.identifyUri,
      resolveHome: expansion.resolveHome,
      fetchPage,
      expandForNavigation,
    },
  };
}
