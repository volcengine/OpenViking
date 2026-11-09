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
import type { HTMLAttributes, ReactNode } from 'react';

/**
 * 文件浏览模块的可替换文案。树与预览各有自己的一套，分别透传，模块外壳只提供
 * 自己渲染的那几条。
 *
 * Replaceable copy of the file browser module. The tree and the preview each keep
 * their own set, passed through as-is; the module shell only carries the strings
 * it renders itself.
 */
export interface FileBrowserLabels {
  /** 未选中资源时的提示。Message shown when no resource is selected. */
  empty?: string;
  /** 加载失败提示。Load failure message. */
  error?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
  /** 刷新按钮的无障碍名称。Accessible name of the refresh control. */
  refresh?: string;
  /** 搜索框占位文案。Search placeholder. */
  searchPlaceholder?: string;
  /** 搜索无结果提示。Message shown when a search returns nothing. */
  searchEmpty?: string;
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
  /** 目录树文案。Copy of the file tree. */
  tree?: FileTreeLabels;
  /** 预览文案。Copy of the file preview. */
  preview?: FilePreviewLabels;
}

/**
 * 文件浏览模块的属性。
 *
 * 模块自己通过 Hook 取数，props 里没有数据字段：宿主给配置、受控的当前资源、
 * 工具栏入口、文案与请求配置。目录、搜索、预览页签与行级状态都在模块内维护。
 *
 * 模块只覆盖文件树与文件预览两块。命令面板属于 `console`。
 *
 * Props of the file browser module.
 *
 * The module loads its own data through a Hook, so no prop carries data: the
 * host supplies configuration, the controlled current resource, the toolbar
 * entries, copy and request configuration. The directory, search, preview tabs
 * and per-row state all live inside the module.
 *
 * The module covers only the file tree and the file preview. The command panel
 * belongs to `console`.
 */
export interface FileBrowserProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onSelect' | 'children'
> {
  /**
   * 受控的当前资源 URI。传入时由宿主决定当前资源，模块只上报切换意图；显式传
   * `null` 表示清空选中，模块不会回退到内部记下的上一个资源。
   *
   * 用 `uri !== undefined` 判断是否受控，而不是真假值：`null` 是一个有效的受控
   * 值（「什么都没选中」），与「没有传这个属性」（非受控）是两回事。
   *
   * The controlled current resource URI. When given, the host owns the current
   * resource and the module only reports switch intent; an explicit `null` clears the
   * selection instead of falling back to the last resource the module remembers.
   *
   * Control is detected with `uri !== undefined` rather than truthiness: `null` is a
   * valid controlled value ("nothing selected") and differs from "the prop is absent"
   * (uncontrolled).
   */
  uri?: string | null;
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
   * 透传给预览底座的文案语言。组件自身文案仍由 `labels` 控制。
   * Language of the preview base's built-in copy. The module's own copy stays
   * with `labels`.
   */
  locale?: 'en-US' | 'zh-CN';
  /**
   * 工具栏上方的宿主入口，例如连接 Agent、导入资源。
   *
   * 两个固定入口由 `FileBrowserEntries` 提供，外观、图标、分隔线与等分排布都
   * 在那边，宿主只决定显示哪几个与点击后做什么。搜索与刷新由模块自己渲染。
   *
   * Host entries above the toolbar, for example connecting an agent or importing
   * resources.
   *
   * The two fixed entries come from `FileBrowserEntries`, which owns their look,
   * icons, divider and equal-width layout; the host only decides which appear and
   * what a click does. Search and refresh are rendered by the module itself.
   */
  toolbarActions?: ReactNode;
  /**
   * 当前资源变化后触发，参数为新的 URI；清除选中时传入 `null`。
   * Called after the current resource changes, with the new URI; `null` when
   * the selection is cleared.
   */
  onUriChange?: (uri: string | null) => void;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
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
  /** 按渲染顺序排列的可见行。Visible rows in render order. */
  rows: readonly FileTreeRow[];
  /** 当前选中的行 id。Selected row id. */
  selectedId: string | null;
  /** 当前资源 URI。Current resource URI. */
  currentUri: string | null;
  /** 当前查询词。The current query. */
  query: string;
  /** 是否正在展示搜索结果。Whether search results are showing. */
  searching: boolean;
  /** 搜索是否进行中。Whether a search is running. */
  searchLoading: boolean;
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
   * 搜索请求本身的失败原因。与「搜索成功但没有结果」不同：前者要显示原因与重试，
   * 后者才是空态文案。
   *
   * Why the search request failed. It differs from "the search succeeded but matched
   * nothing": the former needs the reason and a retry, only the latter is the empty
   * copy.
   */
  searchError: Error | null;
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
  /** 更新查询词；空串回到目录树。Updates the query; an empty string returns to the tree. */
  setQuery: (query: string) => void;
}
