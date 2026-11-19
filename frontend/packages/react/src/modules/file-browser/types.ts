import type { ExperienceDashboardProps } from '../../components/experience-dashboard/index.js';
import type { DirectoryExpansionSnapshot } from '../../shared/directory-tree/types.js';
import type { ErrorFallback } from '../../shared/error-boundary/types.js';
import type { FileBrowserWorkflowLabels } from './preview-workflow-types.js';
export type {
  FileBrowserComponent,
  FileBrowserHandle,
  FileBrowserOpenResourceOptions,
} from './resource-navigation-types.js';
export type { FileBrowserWorkflowLabels } from './preview-workflow-types.js';
import type {
  FileBrowserPreviewActions,
  FileBrowserPreviewSlots,
} from './preview-types.js';
export type {
  FileBrowserPreviewActions,
  FileBrowserPreviewSlots,
  FileBrowserPreviewActionContext,
} from './preview-types.js';
import type { Locale } from '../../shared/locale.js';
import type { AgentConnectProps } from '../../components/agent-connect/index.js';
import type { FileBrowserActionBarLabels } from '../../components/file-browser-action-bar/index.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import type { FileBrowserDashboard } from './dashboard-model.js';
import type { FilePreviewLabels } from '../../components/file-preview/index.js';
import type {
  FileTreeEntryRow,
  FileTreeLabels,
} from '../../components/file-tree/index.js';
import type { OvRequestOptions } from '../../client/index.js';
import type {
  FileBrowserAclLabels,
  FileBrowserAclOptions,
} from './acl-types.js';
import type { SearchMode } from './search-types.js';
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
  workflows?: Partial<FileBrowserWorkflowLabels>;
  systemDirectory?: string;
  accessDenied?: string;
  traverseOnly?: string;
  /** 权限管理文案。Permission management copy. */
  acl?: FileBrowserAclLabels;
  /** 预览底部字符数。Character count in the preview footer. */
  formatCharacterCount?: (count: number) => string;
  /** 预览底部更新时间的文案。Copy for the formatted update time. */
  formatPreviewUpdatedAt?: (time: string) => string;
  /** 资源预览的模型提示。空字符串可隐藏。Model notice for resources; pass an empty string to hide it. */
  previewModelNotice?: string;
  /** 未选中资源时的提示。Message shown when no resource is selected. */
  empty?: string;
  /** 加载失败提示。Load failure message. */
  error?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
  /** 刷新按钮的无障碍名称。Accessible name of the refresh control. */
  refresh?: string;
  /** 搜索入口的文案。Label of the search trigger. */
  searchPlaceholder?: string;
  /** 内置搜索弹窗的无障碍标题。Accessible title of the built-in search dialog. */
  searchTitle?: string;
  /** 尚未输入查询时的标题。Title shown before any query is entered. */
  searchEmptyTitle?: string;
  /** 尚未输入查询时的说明。Explanation shown before any query is entered. */
  searchHint?: string;
  /** 搜索无结果提示。Message shown when a search returns nothing. */
  searchEmpty?: string;
  /** 搜索请求失败的结论，原因由服务端返回。Conclusion when the search request fails; the reason comes from the server. */
  searchFailed?: string;
  /** 各检索方式的输入提示。Input placeholder of each retrieval mode. */
  searchModePlaceholders?: Record<SearchMode, string>;
  /** 各检索方式的说明。One-line description of each retrieval mode. */
  searchModeHints?: Record<SearchMode, string>;
  /** 高级筛选面板的标题。Title of the advanced filter panel. */
  searchAdvanced?: string;
  /** 清空筛选。Clears the filters. */
  searchClear?: string;
  /** 目录筛选的标题。Title of the directory filter. */
  searchScopes?: string;
  /** 选择目录的入口。Entry that opens the directory picker. */
  searchPickScope?: string;
  /** 目录筛选项为空时的说明。Shown when no scope can be filtered. */
  searchScopesUnsupported?: string;
  /** 内容层级的标题。Title of the content-level filter. */
  searchLevels?: string;
  /** 三级内容的名称。Names of the three content levels. */
  searchLevelLabels?: [string, string, string];
  /** 时间筛选的标题。Title of the time filter. */
  searchTime?: string;
  /** 按创建时间。Filter by creation time. */
  searchCreatedAt?: string;
  /** 按更新时间。Filter by update time. */
  searchUpdatedAt?: string;
  /** 起始时间。Start of the time range. */
  searchSince?: string;
  /** 结束时间。End of the time range. */
  searchUntil?: string;
  /** 忽略大小写。Case-insensitive matching. */
  searchIgnoreCase?: string;
  /** 某个筛选在当前检索方式下不可用的原因。Why a filter is unavailable in the current mode. */
  searchFilterUnsupported?: string;
  /** 检索结果数量，参数为条数。Result count; receives the number. */
  searchResultCount?: (count: number) => string;
  /** 模式标签旁的说明。Description beside the mode chip. */
  searchVia?: string;
  /** 切换检索方式的入口。Entry that switches the retrieval mode. */
  searchSwitchMode?: string;
  /** 高级筛选按钮的无障碍名称。Accessible name of the advanced-filter button. */
  searchAdvancedLabel?: string;
  /** 搜索进行中的提示。Message shown while a search runs. */
  searchLoading?: string;
  searchDismiss?: string;
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
  importPack?: string;
  exportPack?: string;
  deleteFile?: string;
  deleteTitle?: string;
  deleteMessage?: (name: string) => string;
  deleteFailed?: string;
  deleteCancel?: string;
  deleteConfirm?: string;
  /** 视图切换分组的无障碍名称。Accessible name of the view-switch group. */
  viewSwitch?: string;
  /** 树与详情之间分隔条的无障碍名称。Accessible name of the divider between the tree and the detail. */
  resizeTree?: string;
  /** 目录预览的 L0 摘要页签。The L0 summary tab of a directory preview. */
  l0Summary?: string;
  /** 目录预览的 L1 总览页签。The L1 overview tab of a directory preview. */
  l1Overview?: string;
  /** L0 摘要为空时的提示。Message shown when the L0 summary is absent. */
  emptyL0Summary?: string;
  /** L1 总览为空时的提示。Message shown when the L1 overview is absent. */
  emptyL1Overview?: string;
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
  /** 操作栏与接入抽屉文案。Copy of the action bar and connection drawer. */
  actionBar?: FileBrowserActionBarLabels;
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
 * 接入配置、导入交互、文案与请求配置。操作栏、目录、搜索、预览页签与行级
 * 状态都在模块内维护，包含基础搜索弹窗与接口调用。
 *
 * 模块包含文件树、文件预览与顶部操作栏。命令面板属于 `console`。
 *
 * Props of the file browser module.
 *
 * The module loads its own data through a Hook, so no prop carries data: the
 * host supplies configuration, the controlled current resource, import interactions,
 * copy and request configuration. The action bar, directory, search, preview tabs
 * and per-row state all live inside the module, including basic search UI and requests.
 *
 * The module includes the file tree, file preview and top action bar. The command panel
 * belongs to `console`.
 */
/** Serializable expansion state for one FileBrowser root and identity scope. */
export type FileBrowserExpansionSnapshot = DirectoryExpansionSnapshot;

export interface FileBrowserProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onSelect' | 'children'
> {
  /**
   * 共享资源 ACL；默认关闭。开启后显示有效权限并通过 Modal 管理授权。
   * Shared-resource ACL, off by default. Shows effective permissions and manages grants in a Modal.
   */
  acl?: boolean | FileBrowserAclOptions;
  /** 禁用预览编辑、导入与文件删除。Disables preview editing, import and file deletion. */
  readOnly?: boolean;
  /** 内置预览操作的显隐开关，默认按资源能力显示。Visibility flags for native preview operations. */
  previewActions?: FileBrowserPreviewActions;
  /** 可选文案语言；继承上层，最终默认英文。Optional locale; inherits or defaults to en-US. */
  locale?: Locale;
  /**
   * 受控的当前资源 URI。挂载后由宿主切换 URI 时，模块打开预览并定位目录树。
   *
   * 用 `uri !== undefined` 判断是否受控，而不是真假值：空字符串也算宿主接管。
   * 模块不会主动清空选中——点目录行与点面包屑祖先都带具体 URI，没有「取消选中」
   * 的交互——因此这里不接受 `null`。
   *
   * The controlled current resource URI. Host-driven changes after mount open
   * the preview and reveal the resource in the tree.
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
   * 初始展开目录 URI 快照。传入后优先于 defaultExpandedUri，空数组只展开根目录。
   * Initial expanded directory URIs. Takes precedence over defaultExpandedUri
   * when provided. An empty array expands only the root.
   */
  defaultExpandedUris?: readonly string[];
  /** Initial snapshot for this mount. Takes precedence over defaultExpandedUris. */
  defaultExpansionSnapshot?: FileBrowserExpansionSnapshot;
  /**
   * 内置接入抽屉的配置；传入后显示连接入口，省略时隐藏。
   * Configuration of the built-in connection drawer; enables its entry when provided.
   */
  agentConnect?: AgentConnectProps;
  /** 控制内置接入抽屉是否打开。Controls whether the built-in connection drawer is open. */
  agentConnectOpen?: boolean;
  /** Host capability filter. Omitted enables both; an empty array disables discovery. */
  dashboardKinds?: readonly FileBrowserDashboard['kind'][];
  /** Overrides individual requests for the built-in experience dashboard. */
  experienceDataSource?: FileBrowserExperienceDataSource;
  experienceDashboardSlots?: ExperienceDashboardProps['slots'];
  /**
   * 用户请求打开或关闭内置接入抽屉时触发；宿主改变属性时不触发。
   * Called when the user requests to open or close the built-in connection drawer.
   * Host-driven prop changes do not trigger it.
   */
  onAgentConnectOpenChange?: (open: boolean) => void;
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
  /** 用户选中目录树条目时触发，包含重复选中。Reports a tree entry selection, including reselecting the same row. */
  onSelect?: (row: FileTreeEntryRow) => void;
  /**
   * 目录树初始化完成后上报展开快照，不含根目录。恢复失败时，保留仍展开分支中未加载的路径。
   * Reports the expansion snapshot after initialization, excluding the root.
   * When restoration fails, it preserves unresolved paths in open branches.
   */
  onExpandedUrisChange?: (uris: readonly string[]) => void;
  /** Reports expansion and explicit collapses, including unresolved alias edits. */
  onExpansionSnapshotChange?: (snapshot: FileBrowserExpansionSnapshot) => void;
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
  /**
   * 文件与目录的预览操作区。actions 替换默认按钮（含编辑态保存、取消），null 隐藏；
   * extraActions 追加到默认工具栏。自定义 actions 时需自行渲染追加操作与编辑按钮。
   * File and directory preview actions. actions replaces the defaults (including
   * Save/Cancel); null hides them. extraActions appends to the default toolbar.
   * Custom actions render their own extras and editing controls.
   */
  previewSlots?: FileBrowserPreviewSlots;
  /** 渲染异常时的替代内容，省略时继承外层或使用内置展示。Render-failure content; inherits or uses the default when omitted. */
  slots?: { errorFallback?: ErrorFallback };
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
