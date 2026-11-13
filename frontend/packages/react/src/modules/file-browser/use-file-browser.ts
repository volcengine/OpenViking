import { useLocale } from '../../shared/locale-context.js';
import { resolveLabels, resolveSummaryLabels } from './file-browser-labels.js';
import { useMemo, useRef, useState } from 'react';

import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/index.js';
import { useOvClient } from '../../client/provider.js';
import { useFsPreview } from './use-fs-preview.js';
import { dashboardKey, mergeDashboardTab } from './dashboard-model.js';
import { useBrowserDashboards } from './use-browser-dashboards.js';
import { useFsTree } from './use-fs-tree.js';
import { useSummaryLevel } from './use-summary-level.js';
import { useBrowserRefresh } from './use-browser-refresh.js';
import { usePreviewTabs } from './use-preview-tabs.js';
import { findSelectedRowId } from './fs-uri.js';
import type {
  FileBrowserActions,
  FileBrowserApi,
  UseFileBrowserOptions,
} from './types.js';

/**
 * 文件浏览模块的目录与预览联动：目录行展开树，文件行打开预览。
 * 搜索弹窗独立管理查询与筛选，不改变目录树；点中结果时通过 `openResource`
 * 把选中与展开交给这里。
 *
 * Coordinates directory loading and preview selection. The search dialog owns its
 * query and filters separately and leaves the tree unchanged; picking a result hands
 * the selection and expansion back through `openResource`.
 */
export function useFileBrowser({
  rootUri,
  locale: localeProp,
  dashboardKinds,
  renderDashboard,
  dashboardLabels,
  controlled = false,
  summaryLabels,
  accessLabels,
  formatCharacterCount,
  defaultExpandedUri,
  currentUri,
  requestOptions,
  onUriChange,
}: UseFileBrowserOptions): FileBrowserApi {
  const locale = useLocale(localeProp);
  const text = resolveLabels(accessLabels, locale);
  const client = useOvClient();
  const [pendingUri, setPendingUri] = useState<string | null>(currentUri);
  const summaryLevel = useSummaryLevel();
  const { resolve: resolveSummary } = summaryLevel;
  const tree = useFsTree({
    client,
    rootUri,
    defaultExpandedUri,
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
  const reloadTree = useBrowserRefresh(tree.refresh, tree.reveal, activeUri);
  const { identifyUri } = tree;
  const blocked = tree.accessOf(activeUri);
  // 三态而不是布尔：不在已知目录集合里既可能是文件，也可能是还没加载过的目录，
  // 两者要走的接口不同，不能合并成「不是目录」。
  //
  // Three states rather than a boolean: absent from the known directory set may mean a
  // file or a directory that has not been loaded yet, and the two read different
  // endpoints, so they cannot collapse into "not a directory".
  const resourceType = tree.resourceTypeOf(activeUri);

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
      const target = resolveSummary(uri);
      setPendingUri(target);
      notifyUriChange.current(target);
      await openTree(target);
    },
    [openTree, resolveSummary],
  );

  const { rows, openPath, reveal, cancelReveal } = tree;
  const openResource = useMemo(
    () => async (resource: string) => {
      const uri = resolveSummary(resource);
      setPendingUri(uri);
      notifyUriChange.current(uri);
      const directory = tree.directoryUris.has(identifyUri(uri))
        ? uri
        : uri.slice(0, uri.lastIndexOf('/'));
      await openPath(directory);
    },
    [openPath, tree.directoryUris, identifyUri, resolveSummary],
  );

  const boards = useBrowserDashboards({
    rows,
    metadataRows: tree.metadataRows,
    uri: blocked ? null : activeUri,
    rootUri,
    generation: reloadTree.generation,
    locale,
    requestOptions,
    onOpenUri: openResource,
    dashboardKinds,
    renderDashboard,
    dashboardLabels,
  });

  const preview = useFsPreview({
    client,
    // 非受控模式下 currentUri 是 null，真正选中的是 pendingUri；预览要跟着
    // 实际选中的资源走，否则内部维护的选中永远不会加载预览。
    //
    // Under the uncontrolled mode `currentUri` is null and `pendingUri` holds the
    // real selection, so the preview follows that instead — otherwise a selection
    // the module owns would never load anything.
    uri: blocked ? null : activeUri,
    resourceType,
    labels: summaryLabels ?? resolveSummaryLabels(locale),
    formatCharacterCount: formatCharacterCount ?? text.formatCharacterCount,
    openParent,
    generation: reloadTree.generation,
    requestOptions,
    contentKind: boards.contentPending
      ? 'pending'
      : boards.isWiki
        ? 'wiki'
        : 'plain',
  });

  const selectedId = useMemo(
    () => findSelectedRowId(rows, activeUri, identifyUri),
    [rows, activeUri, identifyUri],
  );

  // 解构出具体操作，`select` 里因此不再碰整个 `tree` 对象。
  // Destructure the operations so `select` never touches the whole `tree` object.
  const { open, toggleExpand, loadMore, retry } = tree;

  const { open: openDashboard, close: closeDashboard } = boards;
  const setActiveKey = preview.setActiveKey;
  const navigateResource = useMemo<FileBrowserActions['openResource']>(
    () => async (resource, options) => {
      // 摘要文件不在树的列表里：落到所属目录，层级交给预览。
      // Summary files are not in the tree listing: land on the owning directory and
      // leave the level to the preview.
      const uri = resolveSummary(resource);
      cancelReveal();
      closeDashboard();
      setPendingUri(uri);
      notifyUriChange.current(uri);
      return options?.reveal === false ? true : reveal(uri);
    },
    [cancelReveal, closeDashboard, reveal, resolveSummary],
  );
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
        if (row.disabled || row.selectable === false) return;
        resolveSummary(row.uri);
        cancelReveal();
        closeDashboard();
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
      refresh: reloadTree.refresh,
      openUri: openResource,
      openResource: navigateResource,
      openDashboard: openDashboard,
      setPreviewKey: (key: string) => {
        if (activeUri && key === dashboardKey(activeUri))
          openDashboard(activeUri);
        else {
          closeDashboard();
          setActiveKey(key);
        }
      },
    }),
    // 依赖收窄到具体的操作函数，不依赖整个 `tree`：后者每次展开都换新引用，
    // 会让 `actions` 跟着变，进而把 `FileTree` 的 `operations` 也换掉。
    //
    // Narrow the dependencies to the operations themselves rather than the whole
    // `tree`: the latter gets a new identity on every expansion, which would
    // replace `actions` and with it the `operations` handed to `FileTree`.
    [
      setActiveKey,
      navigateResource,
      cancelReveal,
      open,
      toggleExpand,
      loadMore,
      retry,
      reloadTree.refresh,
      openDashboard,
      closeDashboard,
      openResource,
      resolveSummary,
      activeUri,
    ],
  );

  const previewTabs = usePreviewTabs(
    summaryLevel.decorate(preview.tabs),
    navigateResource,
  );
  const previewError = blocked
    ? new Error(text[blocked === 'denied' ? 'accessDenied' : 'traverseOnly'])
    : preview.error;
  return {
    rows,
    selectedId,
    identifyUri,
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
    error: tree.error ?? previewError,
    treeError: tree.error,
    previewError,
    dashboards: boards.dashboards,
    previewDashboard: boards.current,
    dashboardError: boards.error,
    previewTabs: mergeDashboardTab(previewTabs, boards.tab, Boolean(blocked)),
    previewKey:
      boards.isOpen && activeUri ? dashboardKey(activeUri) : preview.activeKey,
    previewFooter: preview.footer,
    refreshing: reloadTree.refreshing,
    actions,
  };
}
