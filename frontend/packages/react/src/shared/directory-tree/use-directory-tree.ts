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
  const {
    expanded,
    expandedRef,
    initialExpanded,
    hasSavedExpansion,
    setExpanded,
  } = useDirectoryExpansion(
    session,
    activeSession,
    rootUri,
    defaultExpandedUris,
  );
  const initialPath = hasSavedExpansion ? undefined : defaultExpandedUri;
  const { initialLoad, initializing } = useInitializeDirectoryTree({
    session,
    rootUri,
    initialPath,
    savedExpansion: hasSavedExpansion ? initialExpanded : undefined,
    expandedRef,
    setExpanded,
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
        const isOpen = expandedRef.current.has(row.uri);
        setExpanded((previous) => {
          const next = new Set(previous);
          if (isOpen) next.delete(row.uri);
          else next.add(row.uri);
          return next;
        });
        if (!isOpen) await fetchPage(row.uri, 0);
      },
      loadMore: async (uri: string) => {
        await fetchPage(uri, 1);
      },
      retry: async (uri: string) => {
        if (activeSession.current) await fetchPage(uri, 0, undefined, true);
        else retryScope();
      },
      refresh: async () => {
        if (activeSession.current) await activeSession.current.refresh();
        else retryScope();
      },
    }),
    [scope, rootUri, expandedRef, setExpanded, fetchPage, retryScope],
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
  const isReady = dirs[rootUri]?.ready ?? false;
  useReportExpansion(
    session,
    isReady && !isInitializing && !dirs[rootUri]?.error,
    rows,
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
      request,
      scope,
      dirs: session?.dirs ?? emptyDirectoryRef,
      initialLoad,
      expanded: expandedRef,
      fetchPage,
      setExpanded,
    },
  };
}
