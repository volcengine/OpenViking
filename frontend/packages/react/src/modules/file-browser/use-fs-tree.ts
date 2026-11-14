import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { SetStateAction } from 'react';

import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/index.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { useTreeMetadata } from './fs-directories.js';
import { useRequestScope } from './use-request-scope.js';
import { useResourceUriIdentity } from './use-resource-uri-identity.js';
import { useFsTreeReveal } from './use-fs-tree-reveal.js';
import { useFsTreePage } from './use-fs-tree-page.js';
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

export interface FsTreeState extends ReturnType<typeof useTreeMetadata> {
  rows: readonly FileTreeRow[];
  isReady: boolean;
  error: Error | null;
  toggleExpand: (row: FileTreeEntryRow) => Promise<void>;
  open: (uri: string) => Promise<void>;
  openPath: (uri: string) => Promise<void>;
  reveal: (uri: string) => Promise<boolean>;
  cancelReveal: () => void;
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
  const [dirs, setRenderedDirs] = useState<Record<string, DirState>>({});
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
  const initialLoad = useRef<Promise<void> | undefined>(undefined);
  const dirsRef = useRef(dirs);
  // Publish each page to the async reader before React commits the tree. A
  // reveal can consume consecutive pages in the same React batch.
  const setDirs = useCallback(
    (update: SetStateAction<Record<string, DirState>>) => {
      const next =
        typeof update === 'function' ? update(dirsRef.current) : update;
      dirsRef.current = next;
      setRenderedDirs(next);
    },
    [],
  );
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

    initialLoad.current = run().catch((cause: unknown) => {
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
  }, [client, rootUri, defaultExpandedUri, requestOptions, setDirs]);

  // 展开与分页的请求属于某个有效作用域，细节见 `useRequestScope`。
  // Expansion and paging belong to one request scope; see `useRequestScope`.
  const scope = useRequestScope(client, requestOptions, rootUri);
  const identity = useResourceUriIdentity(client, requestOptions, rootUri);

  // 展开与分页操作的引用必须稳定：预览的取数 effect 依赖 `open`，如果它每轮
  // 都变，effect 就会反复重跑并取消上一次请求，预览永远加载不出来。
  //
  // The expansion and paging callbacks must keep a stable identity: the preview's
  // loading effect depends on `open`, and a fresh one every render would re-run the
  // effect and cancel the previous request, leaving the preview permanently empty.
  const fetchPage = useFsTreePage({
    client,
    rootUri,
    requestOptions,
    scope,
    dirsRef,
    expandedRef,
    setDirs,
    setError,
  });
  const state = useMemo(() => {
    // 每次请求都取**当时**的作用域，并把它带到请求结束：作用域切换后旧作用域的
    // 控制器已被中止，因此那次请求既不会写 loading 也不会写结果。refresh 这类跨
    // 多次请求的操作因此不能只读一次控制器——它的后续迭代必须重新取，否则会用旧
    // 客户端发新请求。
    //
    // Each request captures the scope in force **at its start** and carries it to the
    // end: after a switch the old scope's controller is aborted, so that request
    // writes neither the loading flag nor a result. A multi-request operation such as
    // refresh therefore cannot read the controller once — its later iterations must
    // re-read it, or they would issue new requests with the old client.

    return {
      /** 展开目录并按需取子节点。Opens a directory, loading children on demand. */
      open: async (uri: string) => {
        setExpanded((prev) => new Set(prev).add(uri));
        if (!dirsRef.current[uri]) await fetchPage(uri, 0);
      },
      openPath: async (uri: string) => {
        const current = scope.current();
        for (const parent of expandChain(rootUri, uri)) {
          if (scope.current() !== current || current.controller.signal.aborted)
            return;
          setExpanded((prev) => new Set(prev).add(parent));
          if (!dirsRef.current[parent]) await fetchPage(parent, 0);
        }
      },
      /** 在展开与折叠之间切换。Toggles between expanded and collapsed. */
      toggleExpand: async (row: FileTreeEntryRow) => {
        if (row.disabled) return;
        const isOpen = expandedRef.current.has(row.uri);
        setExpanded((prev) => {
          const next = new Set(prev);
          if (isOpen) next.delete(row.uri);
          else next.add(row.uri);
          return next;
        });
        if (!isOpen && !dirsRef.current[row.uri]) await fetchPage(row.uri, 0);
      },
      loadMore: async (parentId: string) => {
        await fetchPage(
          parentId,
          dirsRef.current[parentId]?.entries.length ?? 0,
        );
      },
      retry: async (parentId: string) => {
        await fetchPage(parentId, 0, undefined, true);
      },
      // 刷新是多次请求的循环，因此每一轮都要确认还在同一个作用域里：中途换了账号
      // 就该停下，而不是拿着旧客户端的循环继续往新账号的树上写。
      //
      // Refresh loops over several requests, so every iteration re-checks that it is
      // still in the same scope: an account switch mid-loop has to stop it rather than
      // let it keep writing to the new account's tree with the old client.
      refresh: async () => {
        if (!client) return;
        const current = scope.current();
        const open = [...expandedRef.current];
        setError(null);
        setDirs({});
        for (const uri of open) {
          if (scope.current().key !== current.key) return;
          await fetchPage(uri, 0);
        }
      },
    };
  }, [client, rootUri, scope, fetchPage, setDirs]);

  const navigation = useFsTreeReveal({
    rootUri,
    scope,
    dirs: dirsRef,
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
    isReady,
    error,
    ...state,
    ...navigation,
  };
}
