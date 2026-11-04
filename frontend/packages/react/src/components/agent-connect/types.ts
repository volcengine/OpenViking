import type { HTMLAttributes, ReactNode } from 'react';

/** 步骤中展示的一段代码或配置。A code or configuration snippet shown in a step. */
export interface AgentConnectSnippet {
  /**
   * 片段唯一标识。
   * Unique snippet key.
   */
  key: string;
  /**
   * 代码语言，用于高亮。
   * Snippet language, used for highlighting.
   */
  language: string;
  /**
   * 展示的代码文本。
   * Displayed code text.
   */
  code: string;
  /**
   * 写入剪贴板的文本，可包含真实密钥，与展示内容不同。
   * Text placed on the clipboard instead of `code`, for example with a real key.
   */
  copyText?: string;
  /**
   * 是否初始脱敏展示，例如片段中含有密钥时。
   * Whether the snippet starts masked, for example when it holds a secret.
   */
  concealed?: boolean;
  /**
   * 脱敏状态下揭示按钮的文案。
   * Label of the reveal toggle while concealed.
   */
  revealLabel?: string;
  /**
   * 片段对应的文件名。
   * File name the snippet belongs to.
   */
  filename?: string;
}

/** 接入指南中的一个步骤。One step of the integration guide. */
export interface AgentConnectStep {
  /**
   * 步骤唯一标识。
   * Unique step key.
   */
  key: string;
  /**
   * 步骤标题。
   * Step title.
   */
  title: string;
  /**
   * 步骤说明，由宿主渲染。
   * Step description, rendered by the host.
   */
  description?: ReactNode;
  /**
   * 步骤中的代码或配置片段。
   * Code or configuration snippets of the step.
   */
  snippets?: readonly AgentConnectSnippet[];
  /**
   * 片段之后渲染的补充说明。
   * Additional notes rendered after the snippets.
   */
  notes?: readonly ReactNode[];
  /**
   * 宿主是否认为该步骤已完成。
   * Whether the host considers this step complete.
   */
  done?: boolean;
}

/** 详情视图中的附加页签，例如按协议区分。An extra tab inside the detail view, for example per protocol. */
export interface AgentConnectTab {
  /**
   * 页签唯一标识。
   * Unique tab key.
   */
  key: string;
  /**
   * 页签显示文本。
   * Tab label.
   */
  label: string;
  /**
   * 该页签下的接入步骤。
   * Guide steps under this tab.
   */
  steps?: readonly AgentConnectStep[];
  /**
   * 替代步骤渲染的自由内容，例如宿主提供的 markdown。
   * Rendered instead of steps, for free-form markdown supplied by the host.
   */
  content?: ReactNode;
}

/** 一个可选的接入方式或 Agent。A selectable integration or agent. */
export interface AgentConnectItem {
  /**
   * 条目唯一标识。
   * Unique item key.
   */
  id: string;
  /**
   * 条目名称。
   * Item name.
   */
  name: string;
  /**
   * 条目说明。
   * Item description.
   */
  description?: string;
  /**
   * 宿主提供的图标；缺省时回退到首字母。
   * Logo supplied by the host; falls back to an initial.
   */
  icon?: ReactNode;
  /**
   * 列表分组依据。
   * Grouping used by the catalog list.
   */
  category?: string;
  /**
   * 条目标签。
   * Item tags.
   */
  tags?: readonly string[];
  /**
   * 卡片上的标记，例如「新」「已更新」。
   * Rendered on the card, for example a "new" or "updated" marker.
   */
  badge?: ReactNode;
  /**
   * 是否禁用该条目。
   * Whether the item is disabled.
   */
  disabled?: boolean;
}

/** 一个接入方式的详情数据。Detail data for one integration. */
export interface AgentConnectDetail {
  /**
   * 对应的接入条目。
   * The integration this detail belongs to.
   */
  item: AgentConnectItem;
  /**
   * 接入步骤；提供 `tabs` 时忽略。
   * Guide steps; ignored when `tabs` is provided.
   */
  steps?: readonly AgentConnectStep[];
  /**
   * 附加页签，例如按协议区分。
   * Extra tabs, for example per protocol.
   */
  tabs?: readonly AgentConnectTab[];
  /**
   * 宿主提供的外部链接，例如插件仓库。
   * Host-owned external link, for example the plugin repository.
   */
  docsUrl?: string;
  /**
   * 详情主体是否正在加载。
   * Whether the detail body is loading.
   */
  loading?: boolean;
  /**
   * 详情加载失败时的提示。
   * Shown when the detail failed to load.
   */
  errorMessage?: string;
}

/** 接入或连接状态。Status of the connection. */
export type AgentConnectStatus =
  /**
   * 尚未接入。
   * Not connected yet.
   */
  | 'idle'
  /**
   * 接入进行中。
   * A connection attempt is in progress.
   */
  | 'connecting'
  /**
   * 已接入。
   * Connected.
   */
  | 'connected'
  /**
   * 接入失败。
   * The connection failed.
   */
  | 'error';

/**
 * 可替换文案，组件内置英文默认值。
 * Replaceable copy; the component ships English defaults.
 */
export interface AgentConnectLabels {
  /**
   * 搜索框占位文本。
   * Search input placeholder.
   */
  searchPlaceholder?: string;
  /**
   * 无可选项时的提示。
   * Message shown when there are no items.
   */
  empty?: string;
  /**
   * 返回按钮文案。
   * Back button label.
   */
  back?: string;
  /**
   * 复制按钮文案。
   * Copy button label.
   */
  copy?: string;
  /**
   * 复制成功提示。
   * Message shown after a successful copy.
   */
  copied?: string;
  /**
   * 揭示脱敏内容的按钮文案。
   * Label of the control that reveals concealed content.
   */
  reveal?: string;
  /**
   * 重新隐藏脱敏内容的按钮文案。
   * Label of the control that conceals content again.
   */
  conceal?: string;
  /**
   * 文档入口文案。
   * Documentation entry label.
   */
  docs?: string;
  /**
   * 重试按钮文案。
   * Retry button label.
   */
  retry?: string;
  /**
   * 加载中提示。
   * Loading message.
   */
  loading?: string;
  /**
   * 连接按钮文案。
   * Connect button label.
   */
  connect?: string;
  /**
   * 断开连接按钮文案。
   * Disconnect button label.
   */
  disconnect?: string;
  /**
   * 未连接状态文本。
   * Text for the idle status.
   */
  statusIdle?: string;
  /**
   * 连接中状态文本。
   * Text for the connecting status.
   */
  statusConnecting?: string;
  /**
   * 已连接状态文本。
   * Text for the connected status.
   */
  statusConnected?: string;
  /**
   * 连接失败状态文本。
   * Text for the error status.
   */
  statusError?: string;
  /**
   * 页签区域的无障碍名称。
   * Accessible name of the tab region.
   */
  tabs?: string;
}

/** 接入方式列表视图：宿主提供的全部集成项。Catalog view: the grid of integrations the host offers. */
export interface AgentConnectCatalogProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onCopy' | 'onSelect'
> {
  /**
   * 可选的接入条目。
   * Selectable integration items.
   */
  items: readonly AgentConnectItem[];
  /**
   * 受控的搜索关键字。
   * Controlled search keyword.
   */
  searchKeyword?: string;
  /**
   * 是否显示搜索框。
   * Whether the search input is shown.
   */
  searchable?: boolean;
  /**
   * 受控的分类筛选；为 `null` 时展示全部分类。
   * Controlled category filter; `null` shows every category.
   */
  activeCategory?: string | null;
  /**
   * 可选的分类列表。
   * Available categories.
   */
  categories?: readonly string[];
  /**
   * 列表是否正在加载。
   * Whether the list is loading.
   */
  loading?: boolean;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: AgentConnectLabels;
  /**
   * 无条目时显示的内容。
   * Rendered when there are no items.
   */
  emptyState?: ReactNode;
  /**
   * 搜索关键字变化时触发。
   * Invoked when the search keyword changes.
   */
  onSearchChange?: (keyword: string) => void;
  /**
   * 分类筛选变化时触发。
   * Invoked when the category filter changes.
   */
  onCategoryChange?: (category: string | null) => void;
  /**
   * 选中一个接入条目。
   * Selects an integration item.
   */
  onSelect: (item: AgentConnectItem) => void;
  /**
   * 复制条目的配置，复制内容由宿主拼装。
   * Copies the item's configuration; the host builds the text.
   */
  onCopy?: (item: AgentConnectItem) => void;
}

/** 接入详情视图：一个集成项的接入指南。Detail view: the guide for one integration. */
export interface AgentConnectDetailProps extends Omit<
  HTMLAttributes<HTMLElement>,
  'onCopy'
> {
  /**
   * 详情数据。
   * Detail data.
   */
  detail: AgentConnectDetail;
  /**
   * 受控的当前页签 key。
   * Controlled active tab key.
   */
  activeTabKey?: string;
  /**
   * 当前连接状态。
   * Current connection status.
   */
  status?: AgentConnectStatus;
  /**
   * 状态旁展示的文本，例如已解析的接入地址。
   * Shown next to the status, for example the resolved endpoint.
   */
  statusText?: string;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: AgentConnectLabels;
  /**
   * 详情头部的宿主自有动作，例如打开文档。
   * Host-owned actions in the detail header, for example open docs.
   */
  actions?: ReactNode;
  /**
   * 页签切换时触发。
   * Invoked when the active tab changes.
   */
  onTabChange?: (tabKey: string) => void;
  /**
   * 返回列表视图。
   * Returns to the catalog view.
   */
  onBack?: () => void;
  /**
   * 复制一段代码片段。
   * Copies a snippet.
   */
  onCopy?: (snippet: AgentConnectSnippet) => void;
  /**
   * 重新加载详情。
   * Reloads the detail.
   */
  onRetry?: () => void;
  /**
   * 打开宿主提供的外部文档。
   * Opens the host-provided external documentation.
   */
  onOpenDocs?: () => void;
  /**
   * 发起连接。
   * Starts a connection.
   */
  onConnect?: () => void;
  /**
   * 断开连接。
   * Ends the connection.
   */
  onDisconnect?: () => void;
}

/** 单组件形态：在列表与详情之间切换。Single-component form that switches between catalog and detail. */
export interface AgentConnectProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onCopy' | 'onSelect'
> {
  /**
   * 可选的接入条目。
   * Selectable integration items.
   */
  items: readonly AgentConnectItem[];
  /**
   * 当前详情；为 `null` 或不传时展示列表。
   * Current detail; `null` or omitted renders the catalog.
   */
  detail?: AgentConnectDetail | null;
  /**
   * 受控的搜索关键字。
   * Controlled search keyword.
   */
  searchKeyword?: string;
  /**
   * 是否显示搜索框。
   * Whether the search input is shown.
   */
  searchable?: boolean;
  /**
   * 列表是否正在加载。
   * Whether the list is loading.
   */
  loading?: boolean;
  /**
   * 当前连接状态。
   * Current connection status.
   */
  status?: AgentConnectStatus;
  /**
   * 状态旁展示的文本。
   * Text shown next to the status.
   */
  statusText?: string;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: AgentConnectLabels;
  /**
   * 搜索关键字变化时触发。
   * Invoked when the search keyword changes.
   */
  onSearchChange?: (keyword: string) => void;
  /**
   * 选中一个接入条目。
   * Selects an integration item.
   */
  onSelect: (item: AgentConnectItem) => void;
  /**
   * 返回列表视图。
   * Returns to the catalog view.
   */
  onBack?: () => void;
  /**
   * 复制一段代码片段。
   * Copies a snippet.
   */
  onCopy?: (snippet: AgentConnectSnippet) => void;
  /**
   * 打开宿主提供的外部文档。
   * Opens the host-provided external documentation.
   */
  onOpenDocs?: () => void;
  /**
   * 发起连接。
   * Starts a connection.
   */
  onConnect?: () => void;
  /**
   * 断开连接。
   * Ends the connection.
   */
  onDisconnect?: () => void;
}
