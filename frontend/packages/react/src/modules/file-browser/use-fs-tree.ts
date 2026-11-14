import {
  useEffect,
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
} from 'react';
import type { FileTreeEntryRow } from '../../components/file-tree/index.js';
import { useTreeMetadata } from './fs-directories.js';
import { useRequestScope } from './use-request-scope.js';
import { useResourceUriIdentity } from './use-resource-uri-identity.js';
import { useFsTreeReveal } from './use-fs-tree-reveal.js';
import { expandChain } from './fs-path.js';
import { flattenTree, type DirState } from './fs-model.js';
import { DirectoryQueries } from './directory-queries.js';
import { useQueryScope } from './use-query-scope.js';
import type { FsTreeState, UseFsTreeOptions } from './tree-types.js';

const empty: Record<string, DirState> = {};
const subscribeEmpty = () => () => {};
const emptySnapshot = () => empty;
const emptyDirectoryRef = { current: empty };

export function useFsTree({
  client,
  rootUri,
  defaultExpandedUri,
  requestOptions,
}: UseFsTreeOptions): FsTreeState {
  const {
    request,
    aborted,
    error: identityError,
  } = useQueryScope(client, requestOptions);
  const session = useMemo(
    () => (request ? new DirectoryQueries(request, rootUri) : undefined),
    [request, rootUri],
  );
  const dirs = useSyncExternalStore(
    aborted ? subscribeEmpty : (session?.subscribe ?? subscribeEmpty),
    session?.getSnapshot ?? emptySnapshot,
    emptySnapshot,
  );
  const activeSession = useRef(session);
  // Publish committed query subscriptions for stable event callbacks. Abandoned
  // renders must not replace the session used by the visible preview.
  useEffect(() => {
    activeSession.current = session;
  }, [session]);
  const [local, setLocal] = useState<{
    session: typeof session;
    expanded: ReadonlySet<string>;
  }>();
  const expanded = useMemo(
    () =>
      local && local.session === session ? local.expanded : new Set([rootUri]),
    [local, session, rootUri],
  );
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;
  const initialLoad = useRef<Promise<void> | undefined>(undefined);
  const scope = useRequestScope(
    request?.client ?? null,
    request?.options,
    rootUri,
  );
  const identity = useResourceUriIdentity(
    request?.client ?? null,
    request?.options,
    rootUri,
  );
  const setExpanded = useMemo(
    () => (update: React.SetStateAction<ReadonlySet<string>>) => {
      setLocal((previous) => {
        const session = activeSession.current;
        const current =
          previous && previous.session === session
            ? previous.expanded
            : new Set([rootUri]);
        const next = typeof update === 'function' ? update(current) : update;
        expandedRef.current = next;
        return { session, expanded: next };
      });
    },
    [rootUri],
  );
  // Synchronize the initial/default path with directory subscriptions after commit.
  // Navigation and identity can change without a click. Cleanup only invalidates
  // this navigation; Query observers own shared request cancellation.
  useEffect(() => {
    if (!session) return;
    const controller = new AbortController();
    initialLoad.current = (async () => {
      await session.load(rootUri, 0, controller.signal);
      for (const uri of expandChain(rootUri, defaultExpandedUri)) {
        if (controller.signal.aborted) return;
        const page = await session.load(uri, 0, controller.signal);
        if (page?.error) break;
        if (!controller.signal.aborted)
          setExpanded((previous) => new Set(previous).add(uri));
      }
    })().catch(() => undefined);
    return () => controller.abort();
  }, [session, rootUri, defaultExpandedUri, setExpanded]);
  const fetchPage = useMemo(
    () =>
      async (
        uri: string,
        offset: number,
        signal?: AbortSignal,
        refresh?: boolean,
      ) => {
        try {
          return await activeSession.current?.load(
            uri,
            offset,
            signal,
            refresh,
          );
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
      toggleExpand: async (row: FileTreeEntryRow) => {
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
        await fetchPage(uri, 0, undefined, true);
      },
      refresh: async () => {
        await activeSession.current?.refresh();
      },
    }),
    [scope, rootUri, setExpanded, fetchPage],
  );
  const navigation = useFsTreeReveal({
    rootUri,
    scope,
    dirs: session?.dirs ?? emptyDirectoryRef,
    initialLoad,
    fetchPage,
    setExpanded,
    resolveHome: identity.resolveHome,
    requestSignal: requestOptions?.signal,
  });
  const rows = useMemo(
    () => flattenTree(rootUri, 0, dirs, expanded),
    [rootUri, dirs, expanded],
  );
  return {
    rows,
    ...useTreeMetadata(rootUri, dirs, identity.identifyUri),
    isReady: dirs[rootUri]?.ready ?? false,
    error: identityError ?? dirs[rootUri]?.error ?? null,
    ...state,
    ...navigation,
  };
}
