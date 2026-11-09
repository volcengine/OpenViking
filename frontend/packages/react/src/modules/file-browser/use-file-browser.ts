import { useMemo, useRef, useState } from 'react';

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
  /**
   * 宿主是否受控地持有当前资源。受控时 `currentUri` 就是唯一事实；非受控时
   * `currentUri` 只是初始值，之后的选中由模块自己记在 `pendingUri` 里。
   *
   * 两者的区别只在「谁拥有选中」：非受控模块在初始没有选中时 `currentUri` 也是
   * `null`，因此不能靠值来区分，必须显式声明。
   *
   * Whether the host owns the current resource. While controlled, `currentUri` is the
   * single source of truth; while uncontrolled it is only the initial value and later
   * selections live in the module's own `pendingUri`.
   *
   * The distinction is only about who owns the selection: an uncontrolled module also
   * has a `null` `currentUri` when nothing is selected initially, so the value itself
   * cannot tell the two apart and the mode has to be declared.
   */
  controlled?: boolean;
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
  controlled = false,
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
  // 刷新代次：外部改动后目录、搜索与预览都要重新取数，但它们的依赖（URI、查询
  // 词、客户端）都没有变化，因此需要一个显式信号让三者一起重跑。
  //
  // Refresh generation: after an external change the tree, the search and the
  // preview all need to reload, but none of their dependencies (URI, query, client)
  // changed, so an explicit signal makes all three re-run.
  const [refreshGeneration, setRefreshGeneration] = useState(0);
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
    generation: refreshGeneration,
    requestOptions,
  });
  // 当前资源是目录还是文件，决定预览读摘要还是正文。
  //
  // 判定不能用「可见行里有没有这一行」：根目录本身从不出现在 `tree.rows` 里，
  // 祖先折叠后其后代也从那份展平列表里消失。两者都会被误判成文件，于是根目录
  // 和折叠中的子目录丢掉 L0/L1 摘要、改去读正文。这里改为按树已知的目录集合
  // 判定，它与可见性无关。
  //
  // Whether the current resource is a directory decides whether the preview reads a
  // summary or content.
  //
  // The answer cannot come from "is this row visible": the root itself never appears
  // in `tree.rows`, and collapsing an ancestor removes its descendants from that
  // flattened list. Both would be misread as files, so the root and a nested
  // directory under a collapsed ancestor would lose their L0/L1 summary and read
  // content instead. The decision therefore uses the set of directories the tree
  // knows about, which is independent of visibility.
  // 受控时以宿主给的值为准；非受控时以模块自己记下的选中为准。
  //
  // 非受控**不能**写成 `currentUri ?? pendingUri`：`currentUri` 在非受控模式下只是
  // 初始值，一旦宿主给了非空的初值，这个表达式会永远压过 `pendingUri`，模块自己
  // 上报的选中就再也生效不了，预览也就停在初值上。`pendingUri` 本来就以
  // `currentUri` 为初值，因此只用它即可。
  //
  // While controlled the host's value wins; while uncontrolled the module's own
  // remembered selection wins.
  //
  // Uncontrolled must **not** be written as `currentUri ?? pendingUri`: there
  // `currentUri` is only the initial value, so any non-null initial value would win
  // forever and a selection the module reports could never take effect, leaving the
  // preview stuck on the initial resource. `pendingUri` is seeded from `currentUri`
  // anyway, so using it alone is correct.
  const activeUri = controlled ? currentUri : pendingUri;
  // 三态而不是布尔：不在已知目录集合里既可能是文件，也可能是还没加载过的目录，
  // 两者要走的接口不同，不能合并成「不是目录」。
  //
  // Three states rather than a boolean: absent from the known directory set may mean a
  // file or a directory that has not been loaded yet, and the two read different
  // endpoints, so they cannot collapse into "not a directory".
  const resourceType: 'directory' | 'file' | 'unknown' =
    activeUri === null
      ? 'file'
      : tree.directoryUris.has(activeUri)
        ? 'directory'
        : tree.isKnownFile(activeUri)
          ? 'file'
          : 'unknown';

  // 面包屑上点某一级祖先，就选中它——和点树上的那一行是同一个动作，因此共用
  // 同一条选中路径，而不是另开一个只改预览的入口。
  //
  // 回调引用必须稳定，且不能依赖 `onUriChange`：宿主传内联箭头函数时它每轮都
  // 变，这个回调跟着变，预览的取数 effect 依赖它，于是每次提交都重跑并取消
  // 上一次请求——一次选中就能滚成无限重载。用 ref 读最新的通知函数，依赖只留
  // `openTree`。
  //
  // Clicking an ancestor in the breadcrumb selects it — the same action as clicking
  // that row in the tree, so it goes through the same selection path rather than a
  // second entry point that only changes the preview.
  //
  // The callback must keep a stable identity and must not depend on `onUriChange`:
  // a host passing an inline arrow function changes it every render, which would
  // change this callback, and the preview's loading effect depends on it — so every
  // commit would re-run the effect and cancel the previous request, turning one
  // selection into an endless reload. A ref carries the latest notification and
  // only `openTree` remains as a dependency.
  const openTree = tree.open;
  const notifyUriChange = useRef(onUriChange);
  notifyUriChange.current = onUriChange;
  const openParent = useMemo(
    () => async (uri: string) => {
      setPendingUri(uri);
      notifyUriChange.current(uri);
      await openTree(uri);
    },
    [openTree],
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
    resourceType,
    labels: summaryLabels,
    openParent,
    generation: refreshGeneration,
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
    if (!activeUri || search.results === null) return activeUri;
    const index = search.results.findIndex((entry) => entry.uri === activeUri);
    return index < 0 ? activeUri : `${activeUri}#${index}`;
  }, [activeUri, search.results]);

  // 解构出具体操作，`select` 里因此不再碰整个 `tree` 对象。
  // Destructure the operations so `select` never touches the whole `tree` object.
  const { open, toggleExpand, loadMore, retry, refresh } = tree;

  const actions = useMemo<FileBrowserActions>(
    () => ({
      // 目录与文件都上报当前资源：控制台里点目录既展开也选中，右侧显示该目录的
      // 摘要。只展开不上报会让树上看不出选中了哪一行。
      //
      // 先选中再等子节点：首次展开要发请求，若等它回来才上报，用户在此期间点了
      // 别的资源，这个旧回调会把选中改回目录。选中属于点击那一刻的意图，不该被
      // 一次迟到的列表请求覆盖。
      //
      // Both directories and files report the current resource: in the console a
      // directory click expands it and selects it, with its summary on the right.
      // Expanding without reporting would leave no visible selection in the tree.
      //
      // Select first, then wait for the children: a first expansion issues a request,
      // and reporting only after it returns would let this late callback overwrite a
      // resource the user picked in the meantime. The selection belongs to the moment
      // of the click, not to a listing that arrives afterwards.
      select: async (row: FileTreeEntryRow) => {
        setPendingUri(row.uri);
        notifyUriChange.current(row.uri);
        if (row.type === 'directory') await open(row.uri);
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
      // 刷新覆盖三个数据源：目录列表、当前预览与进行中的搜索。只刷树会让右侧
      // 继续显示旧正文、搜索结果继续指向旧路径——外部改动（例如别的会话写了同
      // 一个文件）之后，那是最容易误导人的状态。
      //
      // Refresh covers all three sources: the listings, the current preview and an
      // active search. Reloading only the tree would leave the detail pane showing
      // old content and the search results pointing at stale paths — the most
      // misleading state after an external change such as another session writing
      // the same file.
      refresh: async () => {
        setRefreshing(true);
        try {
          setRefreshGeneration((generation) => generation + 1);
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
    [preview.setActiveKey, open, toggleExpand, loadMore, retry, refresh],
  );

  return {
    rows,
    selectedId,
    // 上报**实际生效**的资源，而不是宿主传进来的那个 prop：非受控时后者只是初值，
    // 选中新的文件之后它不再变化，消费 `currentUri` 的自定义视图与操作会指向一个
    // 与预览不一致的资源。
    //
    // Report the **effective** resource rather than the prop the host passed: while
    // uncontrolled that prop is only the initial value and stops changing after a new
    // selection, so a custom view or action reading `currentUri` would target a
    // different resource than the preview shows.
    currentUri: activeUri,
    isReady: tree.isReady,
    error: tree.error ?? search.error ?? preview.error,
    treeError: tree.error,
    previewError: preview.error,
    searchError: search.error,
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
