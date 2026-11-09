import { useEffect, useMemo, useRef, useState } from 'react';

import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/index.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { expandChain } from './fs-path.js';
import { loadPage } from './fs-page.js';
import {
  emptyDir,
  flattenTree,
  isAbort,
  mergePage,
  toError,
  type DirState,
} from './fs-model.js';

export interface FsTreeState {
  rows: readonly FileTreeRow[];
  isReady: boolean;
  error: Error | null;
  /**
   * 树已知的目录 URI 集合，与当前是否可见无关。
   * Directory URIs the tree knows about, whether or not they are visible.
   */
  directoryUris: ReadonlySet<string>;
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
  // 就会重跑并取消上一次请求。用 ref 读最新值，依赖只留 `client` 与 `requestOptions`。
  //
  // The callbacks read the latest state but keep a stable identity: the preview's
  // loading effect depends on `open`, and a new identity would re-run it and cancel
  // the previous request. A ref carries the value; only `client` and
  // `requestOptions` remain as dependencies.
  const dirsRef = useRef(dirs);
  dirsRef.current = dirs;
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;

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
      const root = await loadPage(
        client,
        rootUri,
        0,
        controller.signal,
        requestOptions,
      );
      if (controller.signal.aborted) return;
      setDirs({ [rootUri]: mergePage(undefined, root, 0) });

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
          page = await loadPage(
            client,
            uri,
            0,
            controller.signal,
            requestOptions,
          );
        } catch (cause) {
          if (isAbort(cause) || controller.signal.aborted) return;
          break;
        }
        if (controller.signal.aborted) return;
        setExpanded((prev) => new Set(prev).add(uri));
        setDirs((prev) => ({ ...prev, [uri]: mergePage(prev[uri], page, 0) }));
      }
      if (controller.signal.aborted) return;
      setReady(true);
    };

    void run().catch((cause: unknown) => {
      if (isAbort(cause) || controller.signal.aborted) return;
      const failure = toError(cause);
      setError(failure);
      // 根目录失败也要写进这一层的状态：`flattenTree` 只从 `dirs` 生成错误行，
      // 只设整树级的 `error` 会让树渲染出「没有文件」，把 403 说成目录为空。
      //
      // A root failure is written into this level's state too: `flattenTree` only
      // produces an error row from `dirs`, so setting the tree-level `error` alone
      // makes the tree render "no files", reporting a 403 as an empty directory.
      setDirs({
        [rootUri]: { ...emptyDir(), loading: false, error: failure },
      });
      setReady(true);
    });

    return () => controller.abort();
  }, [client, rootUri, defaultExpandedUri, requestOptions]);

  // 展开与分页的请求属于某个客户端作用域。切到另一个 Provider（另一个账号、
  // 另一个服务地址）时，上一个作用域里还没回来的请求必须作废：否则 A 的响应
  // 会在 B 的树上落地，出现一批 B 从未请求过的子节点。
  //
  // 控制器在客户端身份变化时同步替换：放在渲染里判断而不是 effect 里，是因为
  // 下面的回调在同一轮渲染中就要读到新作用域的 signal，而 effect 要等提交之后。
  //
  // Expansion and paging requests belong to one client scope. Switching to another
  // Provider — another account or service address — must invalidate the requests
  // still in flight: otherwise A's response lands on B's tree and shows children B
  // never asked for.
  //
  // The controller is swapped during render rather than in an effect, because the
  // callbacks below need the new scope's signal in the same render, while an effect
  // only runs after commit.
  const scopeRef = useRef({ client, controller: new AbortController() });
  if (scopeRef.current.client !== client) {
    scopeRef.current.controller.abort();
    scopeRef.current = { client, controller: new AbortController() };
  }

  // 卸载时中止当前作用域的请求，避免回调写进已卸载的组件。
  // Abort the current scope's requests on unmount, so a late callback cannot write
  // into an unmounted component.
  useEffect(() => () => scopeRef.current.controller.abort(), []);

  // 展开与分页操作的引用必须稳定：预览的取数 effect 依赖 `open`，如果它每轮
  // 都变，effect 就会反复重跑并取消上一次请求，预览永远加载不出来。
  //
  // The expansion and paging callbacks must keep a stable identity: the preview's
  // loading effect depends on `open`, and a fresh one every render would re-run the
  // effect and cancel the previous request, leaving the preview permanently empty.
  const state = useMemo(() => {
    const fetchPage = async (uri: string, offset: number) => {
      if (!client) return;
      // 每次请求取当时作用域的 signal：作用域切换后，旧请求的回调读到的是已
      // 中止的信号，因此既不会写 loading，也不会写结果。
      //
      // Each request captures the signal of the scope in force at its start: after a
      // scope switch an old callback sees an aborted signal, so it writes neither
      // the loading flag nor a result.
      const { signal } = scopeRef.current.controller;
      if (signal.aborted) return;
      setDirs((prev) => ({
        ...prev,
        [uri]: { ...(prev[uri] ?? emptyDir()), loading: true, error: null },
      }));
      try {
        const page = await loadPage(
          client,
          uri,
          offset,
          signal,
          requestOptions,
        );
        // 成功也要复查：响应可能在上一次作用域里就已经在途，回来时作用域已经换
        // 了，这批子节点不属于当前账号。
        //
        // Successes are re-checked too: the response may have been in flight when
        // the scope changed, and those children belong to the previous account.
        if (signal.aborted) return;
        setDirs((prev) => ({
          ...prev,
          [uri]: mergePage(prev[uri], page, offset),
        }));
      } catch (cause) {
        if (isAbort(cause) || signal.aborted) return;
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
  }, [client, requestOptions]);

  const rows = useMemo(
    () => flattenTree(rootUri, 0, dirs, expanded),
    [rootUri, dirs, expanded],
  );

  // 树已知的目录集合。它与「当前可见的行」无关：根目录从不出现在 `rows` 里，
  // 祖先折叠后其后代也不再出现，但它们的类型不会因此改变。预览据此判断读摘要
  // 还是读正文，所以这份集合必须覆盖已加载过的每一层。
  //
  // The directories the tree knows about. It is independent of the rows currently
  // visible: the root never appears in `rows`, and a descendant disappears when its
  // ancestor collapses, yet neither changes type. The preview decides between a
  // summary and content from this set, so it has to cover every loaded level.
  const directoryUris = useMemo(() => {
    const known = new Set<string>([rootUri]);
    for (const [parent, level] of Object.entries(dirs)) {
      known.add(parent);
      for (const entry of level.entries) {
        if (entry.isDir === true) known.add(entry.uri);
      }
    }
    return known;
  }, [rootUri, dirs]);

  return { rows, isReady, error, directoryUris, ...state };
}
