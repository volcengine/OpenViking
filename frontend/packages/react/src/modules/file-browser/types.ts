import type { Locale } from '../../shared/locale.js';
import type { AgentConnectProps } from '../../components/agent-connect/index.js';
import type { FileBrowserEntriesLabels } from '../../components/file-browser-entries/index.js';
import type { FileBrowserDashboard } from './dashboard-model.js';
import type {
  FilePreviewLabels,
  FilePreviewTab,
} from '../../components/file-preview/index.js';
import type {
  FileTreeEntryRow,
  FileTreeLabels,
  FileTreeRow,
} from '../../components/file-tree/index.js';
import type { OvRequestOptions } from '../../client/index.js';
import type {
  FileBrowserAclLabels,
  FileBrowserAclOptions,
} from './acl-types.js';
import type {
  ComponentPropsWithRef,
  HTMLAttributes,
  ReactElement,
  ReactNode,
} from 'react';

/**
 * 文件浏览模块的可替换文案。入口、树与预览的文案分别透传，模块外壳只提供
 * 自己渲染的那几条。
 *
 * Replaceable copy of the file browser module. Entry, tree and preview copy is
 * passed through as-is; the module shell only carries the strings
 * it renders itself.
 */
export interface FileBrowserDashboardLabels {
  experienceTitle?: string;
  wikiTitle?: string;
  openExperience?: string;
  openWiki?: string;
  wikiConflicts?: string;
  failed?: string;
}

export interface FileBrowserLabels {
  accessDenied?: string;
  traverseOnly?: string;
  /** 权限管理文案。Permission management copy. */
  acl?: FileBrowserAclLabels;
  /** 预览底部字符数。Character count in the preview footer. */
  formatCharacterCount?: (count: number) => string;
  /** 未选中资源时的提示。Message shown when no resource is selected. */
  empty?: string;
  /** 加载失败提示。Load failure message. */
  error?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
  /** 刷新按钮的无障碍名称。Accessible name of the refresh control. */
  refresh?: string;
  /** 搜索入口的占位文案。Placeholder of the search trigger. */
  searchPlaceholder?: string;
  /** 内置搜索弹窗标题。Title of the built-in search dialog. */
  searchTitle?: string;
  /** 搜索提交按钮。Search submit button. */
  searchAction?: string;
  /** 尚未提交查询时的提示。Hint before a query is submitted. */
  searchHint?: string;
  /** 搜索无结果提示。Message shown when a search returns nothing. */
  searchEmpty?: string;
  /** 搜索请求失败的结论，原因由服务端返回。Conclusion when the search request fails; the reason comes from the server. */
  searchFailed?: string;
  /** 搜索进行中的提示。Message shown while a search runs. */
  searchLoading?: string;
  /** 预览读取正文时的提示。Message shown while the preview reads a resource. */
  loading?: string;
  /** 分段器里的「预览」。The "preview" segment label. */
  previewMode?: string;
  /** 分段器里的「源码」。The "source" segment label. */
  sourceMode?: string;
  /** 复制路径按钮的文案。Label of the copy-path button. */
  copyPath?: string;
  /** 复制正文按钮的无障碍名称。Accessible name of the copy-content button. */
  copyContent?: string;
  /** 下载按钮的无障碍名称。Accessible name of the download button. */
  download?: string;
  /** 版本历史按钮的无障碍名称。Accessible name of the version-history button. */
  history?: string;
  /** 视图切换分组的无障碍名称。Accessible name of the view-switch group. */
  viewSwitch?: string;
  /** 树与详情之间分隔条的无障碍名称。Accessible name of the divider between the tree and the detail. */
  resizeTree?: string;
  /** 目录预览的 L0 摘要页签。The L0 summary tab of a directory preview. */
  l0Summary?: string;
  /** 目录预览的 L1 总览页签。The L1 overview tab of a directory preview. */
  l1Overview?: string;
  /** 目录某一级摘要尚未生成时的提示。Message shown when a summary level is empty. */
  summaryEmpty?: string;
  /**
   * 目录某一级摘要请求失败、且服务端没给消息时的兜底文案。与 `summaryEmpty` 是两
   * 件事：前者是「取不到」，后者是「还没生成」，处置完全不同。
   *
   * Fallback copy when a summary level's request failed without a server message. It
   * differs from `summaryEmpty`: the former is "could not load", the latter is "not
   * generated yet", and the two call for different actions.
   */
  summaryFailed?: string;
  /** 入口条与接入抽屉文案。Copy of the entry row and connection drawer. */
  entries?: FileBrowserEntriesLabels;
  /** 目录树文案。Copy of the file tree. */
  tree?: FileTreeLabels;
  dashboard?: FileBrowserDashboardLabels;
  /** 预览文案。Copy of the file preview. */
  preview?: FilePreviewLabels;
}

/**
 * 文件浏览模块的属性。
 *
 * 模块自己通过 Hook 取数，props 里没有数据字段：宿主给配置、受控的当前资源、
 * 接入配置、导入交互、文案与请求配置。入口条、目录、搜索、预览页签与行级
 * 状态都在模块内维护，包含基础搜索弹窗与接口调用。
 *
 * 模块包含文件树、文件预览与顶部入口。命令面板属于 `console`。
 *
 * Props of the file browser module.
 *
 * The module loads its own data through a Hook, so no prop carries data: the
 * host supplies configuration, the controlled current resource, import interactions,
 * copy and request configuration. The entry row, directory, search, preview tabs
 * and per-row state all live inside the module, including basic search UI and requests.
 *
 * The module includes the file tree, file preview and top entries. The command panel
 * belongs to `console`.
 */
export interface FileBrowserProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onSelect' | 'children'
> {
  /**
   * 共享资源 ACL；默认关闭。开启后显示有效权限并通过 Modal 管理授权。
   * Shared-resource ACL, off by default. Shows effective permissions and manages grants in a Modal.
   */
  acl?: boolean | FileBrowserAclOptions;
  /** 隐藏文件编辑入口。Disables editing in the preview. */
  readOnly?: boolean;
  /** 可选文案语言；继承上层，最终默认英文。Optional locale; inherits or defaults to en-US. */
  locale?: Locale;
  /**
   * 受控的当前资源 URI。传入时由宿主决定当前资源，模块只上报切换意图。
   *
   * 用 `uri !== undefined` 判断是否受控，而不是真假值：空字符串也算宿主接管。
   * 模块不会主动清空选中——点目录行与点面包屑祖先都带具体 URI，没有「取消选中」
   * 的交互——因此这里不接受 `null`。
   *
   * The controlled current resource URI. When given, the host owns the current
   * resource and the module only reports switch intent.
   *
   * Control is detected with `uri !== undefined` rather than truthiness: even an empty
   * string counts as the host taking over. The module never clears the selection —
   * both the row click and the breadcrumb ancestor carry a concrete URI, and there is
   * no "deselect" interaction — so `null` is not accepted here.
   */
  uri?: string;
  /**
   * 非受控时的初始资源 URI。省略时不选中任何资源。
   * Initial resource URI when uncontrolled. Nothing is selected when omitted.
   */
  defaultUri?: string;
  /**
   * 目录树的根，缺省为 `viking://`。宿主据此把浏览范围收敛到某个子树。
   * Root of the tree, `viking://` by default. A host narrows the browsed scope
   * with it.
   */
  rootUri?: string;
  /**
   * 首次加载后自动展开到的路径，逐级展开并加载。省略时只展开根，其余全部折叠。
   * 与控制台一致：进去就能看到默认位置，不必逐层点开。
   *
   * Path to expand to after the first load, opening and loading each level. When
   * omitted only the root is expanded and everything else stays collapsed,
   * matching the console: the default location is visible right away.
   */
  defaultExpandedUri?: string;
  /**
   * 内置接入抽屉的配置；传入后显示连接入口，省略时隐藏。
   * Configuration of the built-in connection drawer; enables its entry when provided.
   */
  agentConnect?: AgentConnectProps;
  /** Change this value after a host mutation to reload the tree and preview. */
  refreshKey?: number;
  /** Additional actions alongside the selected resource's native controls. */
  previewActions?: ReactNode;
  /** Host capability filter. Omitted enables both; an empty array disables discovery. */
  dashboardKinds?: readonly FileBrowserDashboard['kind'][];
  /**
   * 当前资源变化后触发，参数为新的 URI。
   *
   * 签名允许 `null`（上游契约），但本模块目前不会主动上报它：没有「取消选中」的
   * 交互，模块也不会自己清空选中。宿主不要依赖收到 `null` 来收起详情区。
   *
   * Called after the current resource changes, with the new URI.
   *
   * The signature allows `null` (the upstream contract), but this module never reports
   * it today: there is no "deselect" interaction and the module does not clear the
   * selection itself. A host must not rely on receiving `null` to collapse the detail
   * pane.
   */
  onUriChange?: (uri: string | null) => void;
  /** Use the host search when it supplies its own search dialog. */
  onSearch?: () => void;
  /** Called after the user refreshes the tree. */
  onRefresh?: () => void;
  /** Reports whether the preview has an open editable draft. */
  onEditingChange?: (editing: boolean) => void;
  /** Reports the URI after a successful preview save. */
  onSaved?: (uri: string) => void;
  /** Host gateway/view override; nullish values retain the native dashboard. */
  renderDashboard?: (dashboard: FileBrowserDashboard) => ReactNode;
  /**
   * 默认导入按钮的点击回调，单独传入即可显示导入入口。
   * Click handler of the default import button; enables its entry on its own.
   */
  onImportResources?: () => void;
  /**
   * 自定义导入入口，参数是带图标与文案的默认原生按钮。
   * 宿主可用自己的浮层包装按钮，保留点击回调。省略时使用默认按钮；返回 null
   * 隐藏导入入口。同时省略点击回调和插槽时不显示导入入口。
   * Renders the import entry using its default native button, including icon and
   * label and click handler. Omit to use the default button, or return null to hide
   * the entry. Omitting both the click handler and renderer hides the entry.
   */
  renderImportResources?: (
    trigger: ReactElement<ComponentPropsWithRef<'button'>>,
  ) => ReactNode;
  /** Adds an optional tab alongside the native preview and dashboards. */
  renderResourceTab?: (resource: {
    uri: string;
    isDirectory: boolean;
    content?: string;
  }) => { title: string; content: ReactNode } | undefined;
  /**
   * 可替换文案，未提供的项使用当前 locale 的内置文案。
   * Replaceable copy; omitted fields follow locale.
   */
  labels?: FileBrowserLabels;
  /**
   * 请求配置覆盖，逐字段覆盖最近的 Provider。
   * Request configuration override, applied field by field over the nearest
   * Provider.
   */
  requestOptions?: OvRequestOptions;
}

/**
 * 模块 Hook 返回的视图数据与操作。宿主可以只用它自行组装视图，不需要组合
 * 视图。
 *
 * The view data and operations the module Hook returns. A host may assemble its
 * own view from these without the composite one.
 */
export interface FileBrowserApi {
  dashboards: ReadonlyMap<string, FileBrowserDashboard>;
  dashboardError: Error | null;
  /** 按渲染顺序排列的可见行。Visible rows in render order. */
  rows: readonly FileTreeRow[];
  /** 当前选中的行 id。Selected row id. */
  selectedId: string | null;
  /** 当前资源 URI。Current resource URI. */
  currentUri: string | null;
  /** 刷新是否进行中。Whether a refresh is running. */
  refreshing: boolean;
  /** 预览底部元信息，例如字符数与更新时间；没有时为 `undefined`。 */
  previewFooter?: string;
  /** 首次加载是否已完成。Whether the first load has finished. */
  isReady: boolean;
  /** 整体加载失败的原因。Reason the whole tree failed to load. */
  error: Error | null;
  /**
   * 目录列表本身的失败原因。与 {@link error} 的区别是来源单一：宿主据此在树的
   * 位置渲染失败态并给出重试，而不是把 403 显示成「没有文件」。
   *
   * Why the directory listing itself failed. It differs from {@link error} by having
   * a single source: a host renders a failure state with a retry in the tree's place
   * instead of showing a 403 as "no files".
   */
  treeError: Error | null;
  /**
   * 当前资源预览的失败原因。选中了资源但读不出来时用它区分「读失败」与「什么
   * 都没选」。
   *
   * Why the preview of the current resource failed. It separates "could not read"
   * from "nothing selected" when a resource is selected.
   */
  previewError: Error | null;
  /** 当前资源的预览页签，没有选中时为 `null`。Preview tabs of the current resource, `null` when nothing is selected. */
  previewTabs: readonly FilePreviewTab[] | null;
  /** 当前预览页签 key。Active preview tab key. */
  previewKey: string | null;
  /** 树与预览的操作。Operations of the tree and the preview. */
  actions: FileBrowserActions;
}

/** 模块 Hook 暴露的操作。Operations the module Hook exposes. */
export interface FileBrowserActions {
  openDashboard: (uri: string) => void;
  /** 选中条目。Selects an entry. */
  select: (row: FileTreeEntryRow) => Promise<void>;
  /** 展开或折叠目录。Expands or collapses a directory. */
  toggleExpand: (row: FileTreeEntryRow) => Promise<void>;
  /** 加载下一页子节点。Loads the next page of children. */
  loadMore: (row: FileTreeRow) => Promise<void>;
  /** 重新加载失败的子节点。Retries a failed children load. */
  retry: (row: FileTreeRow) => Promise<void>;
  /** 重新加载整棵树并保留选中。Reloads the whole tree, keeping the selection. */
  refresh: () => Promise<void>;
  /** 切换预览页签。Switches the preview tab. */
  setPreviewKey: (key: string) => void;
}

/** Internal options for the composed FileBrowser loader. */
export interface UseFileBrowserOptions {
  rootUri: string;
  accessLabels?: Pick<FileBrowserLabels, 'accessDenied' | 'traverseOnly'>;
  /** Built-in copy; inherits the Provider locale, otherwise en-US. */
  locale?: Locale;
  dashboardLabels?: FileBrowserDashboardLabels;
  dashboardKinds?: readonly FileBrowserDashboard['kind'][];
  renderDashboard?: (dashboard: FileBrowserDashboard) => ReactNode;
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
  /**
   * 目录两级摘要的标题与失败兜底文案。两级摘要各自可能请求失败，失败且服务端没给
   * 消息时用 `failure` 显示——它必须可替换，否则宿主无法本地化。
   *
   * Titles of the two summary levels and the fallback copy for a failure. Either level
   * can fail; when it fails without a server message, `failure` is shown — it has to be
   * replaceable, or a host cannot localise it.
   */
  summaryLabels?: { l0: string; l1: string; failure: string };
  /** 字符数文案；省略时跟随语言。Character count copy; follows the resolved locale. */
  formatCharacterCount?: (count: number) => string;
  /** 首次加载后自动展开到的路径。Path to expand to after the first load. */
  defaultExpandedUri?: string;
  currentUri: string | null;
  requestOptions?: OvRequestOptions;
  onUriChange: (uri: string | null) => void;
}
