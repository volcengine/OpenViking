import type { HTMLAttributes, ReactNode } from 'react';

/**
 * 一条执行记录的状态。
 * Status of one execution record.
 */
export type TuiRecordStatus =
  /**
   * 执行中。
   * Still running.
   */
  | 'pending'
  /**
   * 执行成功。
   * Finished successfully.
   */
  | 'success'
  /**
   * 执行失败。
   * Finished with an error.
   */
  | 'error';

/** 检索结果卡片中的一行。One row of a search result card. */
export interface TuiSearchResultItem {
  /**
   * 行唯一标识。
   * Unique row key.
   */
  id: string;
  /**
   * 主标题。
   * Primary title.
   */
  title: string;
  /**
   * 次要标签，例如路径或文件类型。
   * Secondary label, for example a path or file type.
   */
  metaLabel?: string;
  /**
   * 宿主计算出的分数，显示在右侧。
   * Score rendered on the right when the host computes one.
   */
  scoreLabel?: string;
  /**
   * 标题下方的摘录。
   * Short excerpt shown under the title.
   */
  preview?: string;
  /**
   * 该结果对应的资源 URI，可用于打开。
   * URI of the result, which can be opened.
   */
  uri?: string;
}

/** 资源树行内的一条键值明细。One key/value detail row inside a resource tree item. */
export interface TuiResourceTreeDetailRow {
  /**
   * 明细标签。
   * Detail label.
   */
  label: string;
  /**
   * 明细值。
   * Detail value.
   */
  value: string;
}

/** 资源树结果卡片中的一行。One row of a resource tree card. */
export interface TuiResourceTreeItem {
  /**
   * 行唯一标识。
   * Unique row key.
   */
  id: string;
  /**
   * 主标题。
   * Primary title.
   */
  title: string;
  /**
   * 次要标签。
   * Secondary label.
   */
  metaLabel?: string;
  /**
   * 行内展示的键值明细。
   * Key/value rows shown inside the item.
   */
  detailRows?: readonly TuiResourceTreeDetailRow[];
  /**
   * 该资源对应的 URI，可用于打开。
   * URI of the resource, which can be opened.
   */
  uri?: string;
}

/** 状态卡片中的一行键值。One key/value row of a status card. */
export interface TuiStatusRow {
  /**
   * 行标签。
   * Row label.
   */
  label: string;
  /**
   * 行内容，由宿主渲染。
   * Row content, rendered by the host.
   */
  value: ReactNode;
  /**
   * 展示形态：单行、长文本或多行等宽文本。
   * Display form: inline, long text or preformatted text.
   */
  variant?:
    /**
     * 单行展示。
     * Rendered on a single line.
     */
    | 'inline'
    /**
     * 长文本，自动换行。
     * Long text that wraps.
     */
    | 'long-text'
    /**
     * 多行等宽文本。
     * Multi-line preformatted text.
     */
    | 'pre';
}

/** 纯文本结果。A plain text result. */
export interface TuiTextContent {
  /**
   * 变体标记，固定为 `text`。
   * Variant discriminator, always `text`.
   */
  variant: 'text';
  /**
   * 文本正文。
   * Text body.
   */
  text: string;
}

/** Markdown 结果。A Markdown result. */
export interface TuiMarkdownContent {
  /**
   * 变体标记，固定为 `markdown`。
   * Variant discriminator, always `markdown`.
   */
  variant: 'markdown';
  /**
   * Markdown 原文。
   * Markdown source.
   */
  markdown: string;
}

/** JSON 结果。A JSON result. */
export interface TuiJsonContent {
  /**
   * 变体标记，固定为 `json`。
   * Variant discriminator, always `json`.
   */
  variant: 'json';
  /**
   * 待格式化展示的数据。
   * Data to format and display.
   */
  value: unknown;
}

/** 检索结果列表。A list of search results. */
export interface TuiSearchListContent {
  /**
   * 变体标记，固定为 `search-list`。
   * Variant discriminator, always `search-list`.
   */
  variant: 'search-list';
  /**
   * 结果行。
   * Result rows.
   */
  items: readonly TuiSearchResultItem[];
  /**
   * 列表上方的汇总信息，由宿主渲染。
   * Summary above the list, rendered by the host.
   */
  summary?: ReactNode;
}

/** 资源树结果。A resource tree result. */
export interface TuiResourceTreeContent {
  /**
   * 变体标记，固定为 `resource-tree`。
   * Variant discriminator, always `resource-tree`.
   */
  variant: 'resource-tree';
  /**
   * 资源行。
   * Resource rows.
   */
  items: readonly TuiResourceTreeItem[];
  /**
   * 卡片标题。
   * Card title.
   */
  title?: string;
}

/** 状态卡片。A status card. */
export interface TuiStatusContent {
  /**
   * 变体标记，固定为 `status`。
   * Variant discriminator, always `status`.
   */
  variant: 'status';
  /**
   * 键值行。
   * Key/value rows.
   */
  rows: readonly TuiStatusRow[];
  /**
   * 卡片标题。
   * Card title.
   */
  title?: string;
}

/** 写入结果。A write result. */
export interface TuiWriteResultContent {
  /**
   * 变体标记，固定为 `write-result`。
   * Variant discriminator, always `write-result`.
   */
  variant: 'write-result';
  /**
   * 结果行，由宿主渲染。
   * Result lines, rendered by the host.
   */
  lines: readonly ReactNode[];
  /**
   * 卡片标题。
   * Card title.
   */
  title?: string;
}

/** 执行失败。A failed execution. */
export interface TuiErrorContent {
  /**
   * 变体标记，固定为 `error`。
   * Variant discriminator, always `error`.
   */
  variant: 'error';
  /**
   * 失败原因。
   * Failure reason.
   */
  message: string;
}

/** 宿主自定义渲染。Host-defined rendering. */
export interface TuiCustomContent {
  /**
   * 变体标记，固定为 `custom`。
   * Variant discriminator, always `custom`.
   */
  variant: 'custom';
  /**
   * 自定义内容。
   * Custom content.
   */
  content: ReactNode;
}

/**
 * 一条命令产生的结果。宿主选择变体并提供现成数据，控制台只负责布局。
 *
 * What a command produced. The host chooses the variant and supplies already
 * rendered data; the console only lays it out.
 */
export type TuiRecordContent =
  /**
   * 纯文本。
   * Plain text.
   */
  | TuiTextContent
  /**
   * Markdown。
   * Markdown.
   */
  | TuiMarkdownContent
  /**
   * JSON。
   * JSON.
   */
  | TuiJsonContent
  /**
   * 检索结果列表。
   * Search result list.
   */
  | TuiSearchListContent
  /**
   * 资源树。
   * Resource tree.
   */
  | TuiResourceTreeContent
  /**
   * 状态卡片。
   * Status card.
   */
  | TuiStatusContent
  /**
   * 写入结果。
   * Write result.
   */
  | TuiWriteResultContent
  /**
   * 执行失败。
   * Failed execution.
   */
  | TuiErrorContent
  /**
   * 宿主自定义渲染。
   * Host-defined rendering.
   */
  | TuiCustomContent;

/** 一条执行记录。One execution record. */
export interface TuiRecord {
  /**
   * 记录唯一标识。
   * Unique record key.
   */
  id: string;
  /**
   * 提交的命令文本，显示在结果上方。
   * The submitted command text, echoed above the result.
   */
  command: string;
  /**
   * 执行状态。
   * Execution status.
   */
  status: TuiRecordStatus;
  /**
   * 执行结果，进行中时省略。
   * Execution result, omitted while pending.
   */
  content?: TuiRecordContent;
  /**
   * 状态旁的短标签，例如耗时。
   * Short label next to the status, for example a duration.
   */
  metaLabel?: string;
  /**
   * 记录头部的展示时间。
   * Timestamp rendered in the record header.
   */
  createdAt?: string;
  /**
   * 是否可折叠；长输出默认 `true`。
   * Whether the record can be collapsed; defaults to `true` for long output.
   */
  collapsible?: boolean;
}

/** 命令建议列表中的一项。One entry in the command suggestion list. */
export interface TuiCommandSuggestion {
  /**
   * 建议项唯一标识。
   * Unique suggestion key.
   */
  id: string;
  /**
   * 命令名，包含开头的斜杠，例如 `/search`。
   * Command name including the leading slash, for example `/search`.
   */
  name: string;
  /**
   * 命令说明。
   * Command description.
   */
  description?: string;
  /**
   * 用法示例。
   * Usage example.
   */
  usage?: string;
  /**
   * 分组标题；建议按首次出现顺序分组。
   * Grouping heading; items are grouped in first-seen order.
   */
  group?: string;
  /**
   * 是否禁用该建议。
   * Whether the suggestion is disabled.
   */
  disabled?: boolean;
}

/** 控制台为空时展示的快捷入口。A shortcut shown when the console is empty. */
export interface TuiQuickAction {
  /**
   * 快捷入口唯一标识。
   * Unique quick action key.
   */
  key: string;
  /**
   * 按钮显示文本。
   * Button label.
   */
  label: string;
  /**
   * 按钮下方的说明。
   * Description shown under the button.
   */
  description?: string;
  /**
   * 点击后提交的命令文本。
   * Command text submitted on click.
   */
  command: string;
  /**
   * 是否禁用该入口。
   * Whether the shortcut is disabled.
   */
  disabled?: boolean;
}

/** 一条历史命令。A previously submitted command. */
export interface TuiHistoryEntry {
  /**
   * 历史项唯一标识。
   * Unique history entry key.
   */
  id: string;
  /**
   * 历史命令文本。
   * Historical command text.
   */
  command: string;
  /**
   * 提交时间。
   * Submission time.
   */
  createdAt?: string;
}

/**
 * 可替换文案，组件内置英文默认值。
 * Replaceable copy; the component ships English defaults.
 */
export interface TuiLabels {
  /**
   * 输入框占位文本。
   * Input placeholder.
   */
  placeholder?: string;
  /**
   * 发送按钮文案。
   * Send button label.
   */
  send?: string;
  /**
   * 停止按钮文案。
   * Stop button label.
   */
  stop?: string;
  /**
   * 历史按钮文案。
   * History button label.
   */
  history?: string;
  /**
   * 历史为空时的提示。
   * Message shown when the history is empty.
   */
  historyEmpty?: string;
  /**
   * 清空历史按钮文案。
   * Clear-history button label.
   */
  clear?: string;
  /**
   * 建议列表的无障碍名称。
   * Accessible name of the suggestion list.
   */
  suggestions?: string;
  /**
   * 无匹配建议时的提示。
   * Message shown when no suggestion matches.
   */
  noSuggestions?: string;
  /**
   * 快捷入口区域标题。
   * Heading of the quick action area.
   */
  quickActions?: string;
  /**
   * 执行中状态文本。
   * Text for the pending status.
   */
  pending?: string;
  /**
   * 执行成功状态文本。
   * Text for the success status.
   */
  success?: string;
  /**
   * 执行失败状态文本。
   * Text for the error status.
   */
  error?: string;
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
   * 展开按钮文案。
   * Expand button label.
   */
  expand?: string;
  /**
   * 折叠按钮文案。
   * Collapse button label.
   */
  collapse?: string;
  /**
   * 无记录时的提示。
   * Message shown when there are no records.
   */
  empty?: string;
}

/** 命令控制台的公开接口。Public interface of the command console. */
export interface TuiProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onCopy' | 'onSubmit'
> {
  /**
   * 按提交顺序排列的执行记录。
   * Records in submission order.
   */
  records: readonly TuiRecord[];
  /**
   * 受控的输入值。
   * Controlled input value.
   */
  value: string;
  /**
   * 是否有命令正在执行；执行时停止动作替代发送。
   * Whether a command is running; the stop action replaces send.
   */
  running?: boolean;
  /**
   * 首条命令之前替代记录显示的内容。
   * Shown instead of records before the first command.
   */
  emptyState?: ReactNode;
  /**
   * 空态中展示的快捷入口。
   * Shortcuts rendered in the empty state.
   */
  quickActions?: readonly TuiQuickAction[];
  /**
   * 输入聚焦且匹配时展示的建议列表。
   * Suggestion list shown while the input is focused and matches.
   */
  suggestions?: readonly TuiCommandSuggestion[];
  /**
   * 历史弹层中的近期命令。
   * Recent commands for the history popover.
   */
  history?: readonly TuiHistoryEntry[];
  /**
   * 正在预览、尚未重新提交的历史记录。
   * A history entry being previewed before it is submitted again.
   */
  historyPreview?: TuiRecord | null;
  /**
   * 设置后禁止提交并显示该提示。
   * Disables submission and shows this message when set.
   */
  unavailableMessage?: string;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: TuiLabels;
  /**
   * 记录上方的工具栏插槽，例如视图切换。
   * Toolbar slot rendered above the records, for example view switches.
   */
  toolbar?: ReactNode;
  /**
   * 输入框旁的动作插槽，例如用户切换器。
   * Actions rendered next to the input, for example a user switcher.
   */
  inputActions?: ReactNode;
  /**
   * 输入值变化时触发。
   * Invoked when the input value changes.
   */
  onValueChange: (value: string) => void;
  /**
   * 提交一条命令。
   * Submits a command.
   */
  onSubmit: (command: string) => void;
  /**
   * 停止正在执行的命令。
   * Stops the running command.
   */
  onCancel?: () => void;
  /**
   * 选中建议项；宿主可以选择填入输入框而非直接提交。
   * Selects a suggestion; the host may fill the input instead of submitting.
   */
  onSuggestionSelect?: (suggestion: TuiCommandSuggestion) => void;
  /**
   * 选中快捷入口。
   * Selects a quick action.
   */
  onQuickActionSelect?: (action: TuiQuickAction) => void;
  /**
   * 选中历史项。
   * Selects a history entry.
   */
  onHistorySelect?: (entry: TuiHistoryEntry) => void;
  /**
   * 预览历史项。
   * Previews a history entry.
   */
  onHistoryPreview?: (entry: TuiHistoryEntry) => void;
  /**
   * 清空历史。
   * Clears the history.
   */
  onClearHistory?: () => void;
  /**
   * 打开记录中引用的资源。
   * Opens a resource referenced by a record.
   */
  onOpenUri?: (uri: string) => void;
  /**
   * 复制由宿主执行，控制台只上报意图。
   * The host performs the copy; the console only reports the intent.
   */
  onCopy?: (text: string) => void;
}
