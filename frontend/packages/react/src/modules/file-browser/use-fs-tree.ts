import { useEffect, useMemo, useRef, useState } from 'react';

import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/index.js';
import { OV_PATHS } from '../../client/paths.js';
import { expandChain } from './fs-path.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import {
  emptyDir,
  flattenTree,
  isAbort,
  toError,
  type DirState,
  type FsEntry,
} from './fs-model.js';

/** 一次取多少个子节点。One page of children. */
const PAGE_SIZE = 200;

export interface FsTreeState {
  rows: readonly FileTreeRow[];
  isReady: boolean;
  error: Error | null;
  toggleExpand: (row: FileTreeEntryRow) => Promise<void>;
  open: (uri: string) => Promise<void>;
  loadMore: (parentId: string) => Promise<void>;
  retry: (parentId: string) => Promise<void>;
  refresh: () => Promise<void>;
}

export interface UseFsTreeOptions {
  client: OvClient | null;
  rootUri: string;
  /**
   * 首次加载后自动展开到的路径，逐级展开并加载子节点。省略时只展开根。
   * Path to expand to after the first load, opening and loading each level.
   * Omitting it leaves only the root expanded.
   */
  defaultExpandedUri?: string;
  requestOptions?: OvRequestOptions;
}

/**
 * 目录树的取数、展开与分页。按需展开：根目录先加载，展开某个目录时才取它的
 * 子节点，每一层各自持有分页与失败状态，一个目录出错不影响其它分支。
 *
 * Loading, expansion and paging of the directory tree. It loads on demand: the
 * root first, then a directory's children when it expands. Each level keeps its
 * own paging and failure state, so one broken directory does not affect others.
 */
export function useFsTree({
  client,
  rootUri,
  defaultExpandedUri,
  requestOptions,
}: UseFsTreeOptions): FsTreeState {
  const [dirs, setDirs] = useState<Record<string, DirState>>({});
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(
    () => new Set([rootUri]),
  );
  const [isReady, setReady] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // 操作回调要读最新状态，但引用必须稳定：预览的取数 effect 依赖 `open`，一变
  // 就会重跑并取消上一次请求。用 ref 读最新值，依赖只留 `client` 与 `loadPage`。
  //
  // The callbacks read the latest state but keep a stable identity: the preview's
  // loading effect depends on `open`, and a new identity would re-run it and cancel
  // the previous request. A ref carries the value; only `client` and `loadPage`
  // remain as dependencies.
  const dirsRef = useRef(dirs);
  dirsRef.current = dirs;
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;

  const loadPage = useMemo(
    () =>
      async (
        target: OvClient,
        uri: string,
        offset: number,
        signal: AbortSignal,
      ): Promise<{ entries: FsEntry[]; hasMore: boolean }> => {
        const envelope = await target.requestEnvelope<FsEntry[]>({
          method: 'GET',
          path: OV_PATHS.fsLs,
          query: {
            uri,
            output: 'agent',
            node_limit: PAGE_SIZE,
            offset,
            // 不传 `show_all_hidden`：默认列表已排除 `.abstract.md` /
            // `.overview.md` 这类内部摘要文件，它们属于目录的两级摘要，由内容区
            // 的页签展示，不该作为普通文件行出现。靠接口的默认过滤，而不是在
            // 这里按文件名硬编码排除。
            //
            // `show_all_hidden` is deliberately omitted: the default listing already
            // excludes internal summary files such as `.abstract.md` and
            // `.overview.md`. Those are a directory's two summary levels, shown by
            // the content-area tabs, and must not appear as ordinary file rows.
            // Relying on the API's default filter beats hard-coding file names here.
          },
          options: { ...requestOptions, signal },
        });
        return { entries: envelope.result ?? [], hasMore: envelope.hasMore };
      },
    [requestOptions],
  );

  // 根目录随 rootUri、服务地址与身份变化重新加载。请求由 AbortController 取消，
  // 旧结果不会写回；展开状态一并重置，避免指向上一棵树的节点。
  //
  // 给了 `defaultExpandedUri` 时，加载完根目录后沿这条路径逐级展开并取子节点，
  // 让使用者一进来就看到默认位置，而不是从全部折叠开始逐层点。
  //
  // The root reloads when `rootUri`, the service address or the identity changes.
  // The request is cancelled through an AbortController and stale results are
  // dropped; the expansion set resets too, so nothing points at the previous tree.
  //
  // With `defaultExpandedUri`, the path is opened level by level after the root
  // loads, so a user lands on the default location instead of expanding it from a
  // fully collapsed tree.
  useEffect(() => {
    if (!client) return;
    const controller = new AbortController();
    setReady(false);
    setError(null);
    setExpanded(new Set([rootUri]));
    setDirs({ [rootUri]: { ...emptyDir(), loading: true } });

    const run = async () => {
      const root = await loadPage(client, rootUri, 0, controller.signal);
      if (controller.signal.aborted) return;
      setDirs({
        [rootUri]: {
          entries: root.entries,
          hasMore: root.hasMore,
          loading: false,
          error: null,
        },
      });

      const chain = expandChain(rootUri, defaultExpandedUri);
      // 每展开一层就写入展开集合：一次性的 `setExpanded(opened)` 在 Strict Mode
      // 下会被第二次运行的初始重置覆盖，中途读到的仍是旧值。
      //
      // Each level is written to the expansion set as it opens: a single
      // `setExpanded(opened)` at the end is overwritten by the second run's initial
      // reset under Strict Mode, so a read in between still sees the old value.
      for (const uri of chain) {
        // 目标是文件时，最后一段不是目录，取子节点必然失败——停下并保留已展开
        // 的层级即可，不必把它当成错误报给使用者。
        //
        // When the target is a file the last segment is not a directory, so
        // loading children fails by definition: stop and keep the levels already
        // opened instead of reporting it as an error.
        let page;
        try {
          page = await loadPage(client, uri, 0, controller.signal);
        } catch (cause) {
          if (isAbort(cause) || controller.signal.aborted) return;
          break;
        }
        if (controller.signal.aborted) return;
        setExpanded((prev) => new Set(prev).add(uri));
        setDirs((prev) => ({
          ...prev,
          [uri]: {
            entries: page.entries,
            hasMore: page.hasMore,
            loading: false,
            error: null,
          },
        }));
      }
      if (controller.signal.aborted) return;
      setReady(true);
    };

    void run().catch((cause: unknown) => {
      if (isAbort(cause) || controller.signal.aborted) return;
      setError(toError(cause));
      setReady(true);
    });

    return () => controller.abort();
  }, [client, rootUri, defaultExpandedUri, loadPage]);

  // 展开与分页操作的引用必须稳定：预览的取数 effect 依赖 `open`，如果它每轮
  // 都变，effect 就会反复重跑并取消上一次请求，预览永远加载不出来。
  //
  // The expansion and paging callbacks must keep a stable identity: the preview's
  // loading effect depends on `open`, and a fresh one every render would re-run the
  // effect and cancel the previous request, leaving the preview permanently empty.
  const state = useMemo(() => {
    const fetchPage = async (uri: string, offset: number) => {
      if (!client) return;
      setDirs((prev) => ({
        ...prev,
        [uri]: { ...(prev[uri] ?? emptyDir()), loading: true, error: null },
      }));
      try {
        const page = await loadPage(
          client,
          uri,
          offset,
          new AbortController().signal,
        );
        setDirs((prev) => {
          const current = prev[uri] ?? emptyDir();
          return {
            ...prev,
            [uri]: {
              entries:
                offset === 0
                  ? page.entries
                  : [...current.entries, ...page.entries],
              hasMore: page.hasMore,
              loading: false,
              error: null,
            },
          };
        });
      } catch (cause) {
        if (isAbort(cause)) return;
        setDirs((prev) => ({
          ...prev,
          [uri]: {
            ...(prev[uri] ?? emptyDir()),
            loading: false,
            error: toError(cause),
          },
        }));
      }
    };

    return {
      /** 展开目录并按需取子节点。Opens a directory, loading children on demand. */
      open: async (uri: string) => {
        setExpanded((prev) => new Set(prev).add(uri));
        if (!dirsRef.current[uri]) await fetchPage(uri, 0);
      },
      /** 在展开与折叠之间切换。Toggles between expanded and collapsed. */
      toggleExpand: async (row: FileTreeEntryRow) => {
        const isOpen = expandedRef.current.has(row.uri);
        setExpanded((prev) => {
          const next = new Set(prev);
          if (isOpen) next.delete(row.uri);
          else next.add(row.uri);
          return next;
        });
        if (!isOpen && !dirsRef.current[row.uri]) await fetchPage(row.uri, 0);
      },
      loadMore: (parentId: string) =>
        fetchPage(parentId, dirsRef.current[parentId]?.entries.length ?? 0),
      retry: (parentId: string) => fetchPage(parentId, 0),
      refresh: async () => {
        if (!client) return;
        const open = [...expandedRef.current];
        setDirs({});
        for (const uri of open) await fetchPage(uri, 0);
      },
    };
  }, [client, loadPage]);

  const rows = useMemo(
    () => flattenTree(rootUri, 0, dirs, expanded),
    [rootUri, dirs, expanded],
  );

  return { rows, isReady, error, ...state };
}
