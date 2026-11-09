import { useMemo, useState } from 'react';

import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/index.js';
import { useOvClient } from '../../client/provider.js';
import type { OvRequestOptions } from '../../client/types.js';
import { toEntryRow } from './fs-model.js';
import { useFsPreview } from './use-fs-preview.js';
import { useFsSearch } from './use-fs-search.js';
import { useFsTree } from './use-fs-tree.js';
import type { FileBrowserActions, FileBrowserApi } from './types.js';

export interface UseFileBrowserOptions {
  rootUri: string;
  /** 目录两级摘要的标题。Titles of the two summary levels. */
  summaryLabels?: { l0: string; l1: string };
  /** 首次加载后自动展开到的路径。Path to expand to after the first load. */
  defaultExpandedUri?: string;
  currentUri: string | null;
  requestOptions?: OvRequestOptions;
  onUriChange: (uri: string | null) => void;
}

/**
 * 文件浏览模块的取数与联动：把目录树、搜索与预览组合成一份视图数据。三者各自
 * 取数，这里只负责把它们接起来——目录行展开树，文件行与搜索结果打开预览。
 *
 * Data loading and coordination of the file browser module: the tree, the search
 * and the preview each load their own data, and this only wires them together —
 * a directory row expands the tree, while a file row or a search result opens
 * the preview.
 */
export function useFileBrowser({
  rootUri,
  summaryLabels,
  defaultExpandedUri,
  currentUri,
  requestOptions,
  onUriChange,
}: UseFileBrowserOptions): FileBrowserApi {
  const client = useOvClient();
  const [query, setQuery] = useState('');
  const [pendingUri, setPendingUri] = useState<string | null>(currentUri);
  const [refreshing, setRefreshing] = useState(false);
  const tree = useFsTree({
    client,
    rootUri,
    defaultExpandedUri,
    requestOptions,
  });
  const search = useFsSearch({
    client,
    query,
    targetUri: rootUri,
    requestOptions,
  });
  // 当前资源是目录还是文件，决定预览读摘要还是正文。树上能找到这一行就用它的
  // 类型；找不到（例如宿主直接传了一个 URI）按文件处理。
  //
  // Whether the current resource is a directory decides whether the preview reads
  // a summary or content. A row in the tree answers it; when the host passes a URI
  // the tree does not contain, it is treated as a file.
  const activeUri = currentUri ?? pendingUri;
  const isDirectory = useMemo(() => {
    if (!activeUri) return false;
    const row = tree.rows.find(
      (candidate) => candidate.kind === 'entry' && candidate.id === activeUri,
    );
    return row?.kind === 'entry' && row.type === 'directory';
  }, [activeUri, tree.rows]);

  // 面包屑上点某一级祖先，就选中它——和点树上的那一行是同一个动作，因此共用
  // 同一条选中路径，而不是另开一个只改预览的入口。
  //
  // Clicking an ancestor in the breadcrumb selects it — the same action as clicking
  // that row in the tree, so it goes through the same selection path rather than a
  // second entry point that only changes the preview.
  // 只依赖 `tree.open` 本身，不依赖整个 `tree` 对象：后者每次展开都会换新引用，
  // 让这个回调跟着变，进而让预览的取数 effect 反复重跑并取消请求。
  //
  // Depend on `tree.open` alone, never on the whole `tree` object: the latter gets
  // a new identity on every expansion, which would change this callback and make
  // the preview's loading effect re-run and cancel itself.
  const openTree = tree.open;
  const openParent = useMemo(
    () => async (uri: string) => {
      setPendingUri(uri);
      onUriChange(uri);
      await openTree(uri);
    },
    [onUriChange, openTree],
  );

  const preview = useFsPreview({
    client,
    // 非受控模式下 currentUri 是 null，真正选中的是 pendingUri；预览要跟着
    // 实际选中的资源走，否则内部维护的选中永远不会加载预览。
    //
    // Under the uncontrolled mode `currentUri` is null and `pendingUri` holds the
    // real selection, so the preview follows that instead — otherwise a selection
    // the module owns would never load anything.
    uri: activeUri,
    isDirectory,
    labels: summaryLabels,
    openParent,
    requestOptions,
  });

  // 搜索结果的行 id 带序号（同一 URI 可能出现在两个类别里），因此选中项也要按
  // 同样的规则换算，否则选中态在结果列表里对不上任何一行。
  //
  // Search rows carry an index in their id because one URI can appear in two
  // categories, so the selection is converted the same way — otherwise no row in
  // the result list matches it.
  const rows = useMemo<readonly FileTreeRow[]>(() => {
    if (search.results === null) return tree.rows;
    return search.results.map((entry, index) =>
      toEntryRow(entry, 0, false, index),
    );
  }, [search.results, tree.rows]);

  const selectedId = useMemo(() => {
    const uri = currentUri ?? pendingUri;
    if (!uri || search.results === null) return uri;
    const index = search.results.findIndex((entry) => entry.uri === uri);
    return index < 0 ? uri : `${uri}#${index}`;
  }, [currentUri, pendingUri, search.results]);

  // 解构出具体操作，`select` 里因此不再碰整个 `tree` 对象。
  // Destructure the operations so `select` never touches the whole `tree` object.
  const { open, toggleExpand, loadMore, retry, refresh } = tree;

  const actions = useMemo<FileBrowserActions>(
    () => ({
      // 目录与文件都上报当前资源：控制台里点目录既展开也选中，右侧显示该目录的
      // 摘要。只展开不上报会让树上看不出选中了哪一行。
      //
      // Both directories and files report the current resource: in the console a
      // directory click expands it and selects it, with its summary on the right.
      // Expanding without reporting would leave no visible selection in the tree.
      select: async (row: FileTreeEntryRow) => {
        if (row.type === 'directory') await open(row.uri);
        setPendingUri(row.uri);
        onUriChange(row.uri);
      },
      toggleExpand,
      loadMore: async (row: FileTreeRow) => {
        const parentId = 'parentId' in row ? row.parentId : undefined;
        if (parentId) await loadMore(parentId);
      },
      retry: async (row: FileTreeRow) => {
        const parentId = 'parentId' in row ? row.parentId : undefined;
        if (parentId) await retry(parentId);
      },
      refresh: async () => {
        setRefreshing(true);
        try {
          await refresh();
        } finally {
          setRefreshing(false);
        }
      },
      setPreviewKey: preview.setActiveKey,
      setQuery,
    }),
    // 依赖收窄到具体的操作函数，不依赖整个 `tree`：后者每次展开都换新引用，
    // 会让 `actions` 跟着变，进而把 `FileTree` 的 `operations` 也换掉。
    //
    // Narrow the dependencies to the operations themselves rather than the whole
    // `tree`: the latter gets a new identity on every expansion, which would
    // replace `actions` and with it the `operations` handed to `FileTree`.
    [
      onUriChange,
      preview.setActiveKey,
      open,
      toggleExpand,
      loadMore,
      retry,
      refresh,
    ],
  );

  return {
    rows,
    selectedId,
    currentUri,
    isReady: tree.isReady,
    error: tree.error ?? search.error ?? preview.error,
    previewTabs: preview.tabs,
    previewKey: preview.activeKey,
    previewFooter: preview.footer,
    query,
    searching: search.results !== null,
    searchLoading: search.loading,
    refreshing,
    actions,
  };
}
