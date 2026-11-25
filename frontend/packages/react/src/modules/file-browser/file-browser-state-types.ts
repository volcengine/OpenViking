import type { ExperienceDashboardProps } from '../../components/experience-dashboard/index.js';
import type { FileBrowserExpansionSnapshot } from './types.js';
import type { DirectorySummaryLabels } from './file-browser-labels.js';
import type { ReactNode } from 'react';
import type { OvRequestOptions } from '../../client/index.js';
import type { FilePreviewTab } from '../../components/file-preview/index.js';
import type {
  FileTreeEntryRow,
  FileTreeRow,
} from '../../components/file-tree/index.js';
import type { Locale } from '../../shared/locale.js';
import type { Workflow } from '../../shared/monitoring/workflow.js';
import type { FileBrowserDashboard } from './dashboard-model.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import type { FileBrowserOpenResourceOptions } from './resource-navigation-types.js';
import type { FileBrowserDashboardLabels, FileBrowserLabels } from './types.js';
import type { WikiSaveFeedback } from './use-wiki-save-feedback.js';

/** View data and operations for the composed FileBrowser. */
export interface FileBrowserApi {
  treeMonitoring: readonly Workflow<'file_browser_directory_load'>[];
  previewMonitoring?: Workflow<'file_browser_preview_load'>;
  wikiSaveFeedback?: WikiSaveFeedback;
  identifyUri: (uri: string) => string;
  dashboards: ReadonlyMap<string, FileBrowserDashboard>;
  previewDashboard?: FileBrowserDashboard;
  previewIsCompile: boolean;
  previewHasDirectorySummary: boolean;
  dashboardError: Error | null;
  refreshWikiConflicts: () => void;
  /** 按渲染顺序排列的可见行。Visible rows in render order. */
  rows: readonly FileTreeRow[];
  /** 当前选中的行 id。Selected row id. */
  selectedId: string | null;
  /** 当前资源 URI。Current resource URI. */
  currentUri: string | null;
  /** 刷新是否进行中。Whether a refresh is running. */
  refreshing: boolean;
  /** 树和当前预览共用的刷新次数。Refresh revision shared by the tree and active preview. */
  refreshGeneration: number;
  /** 预览字符数文案。Localized preview character count. */
  previewFooter?: string;
  /** 当前资源的更新时间（ISO 8601）。Update time of the current resource (ISO 8601). */
  previewUpdatedAt?: string;
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
  openResource: (
    uri: string,
    options?: FileBrowserOpenResourceOptions,
  ) => Promise<boolean>;
  reveal: (uri: string, preserveExpansion?: boolean) => Promise<boolean>;
  cancelReveal: () => void;
  openUri: (uri: string) => Promise<void>;
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
  hideDeniedEntries?: boolean;
  accessLabels?: Pick<FileBrowserLabels, 'accessDenied' | 'traverseOnly'>;
  /** Built-in copy; inherits the Provider locale, otherwise en-US. */
  locale?: Locale;
  dashboardLabels?: FileBrowserDashboardLabels;
  dashboardKinds?: readonly FileBrowserDashboard['kind'][];
  /** Overrides individual requests for the built-in experience dashboard. */
  experienceDataSource?: FileBrowserExperienceDataSource;
  experienceDashboardSlots?: ExperienceDashboardProps['slots'];
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
  summaryLabels?: DirectorySummaryLabels;
  /** 字符数文案；省略时跟随语言。Character count copy; follows the resolved locale. */
  formatCharacterCount?: (count: number) => string;
  /** 首次加载后自动展开到的路径。Path to expand to after the first load. */
  defaultExpandedUri?: string;
  defaultExpandedUris?: readonly string[];
  defaultExpansionSnapshot?: FileBrowserExpansionSnapshot;
  onExpandedUrisChange?: (uris: readonly string[]) => void;
  onExpansionSnapshotChange?: (snapshot: FileBrowserExpansionSnapshot) => void;
  currentUri: string | null;
  requestOptions?: OvRequestOptions;
  onUriChange: (uri: string | null) => void;
  onSelect?: (row: FileTreeEntryRow) => void;
}
