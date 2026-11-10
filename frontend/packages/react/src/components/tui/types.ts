import type { Locale } from '../../shared/locale.js';
import type { HTMLAttributes, ReactNode } from 'react';
import type {
  TuiCommandDefinition,
  TuiCommandGroup,
} from './command-catalog.js';

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
   * 标题旁的内容层级标签。
   * Content level label next to the title.
   */
  levelLabel?: string;
  /**
   * 分数旁的结果类型标签。
   * Result type label next to the score.
   */
  typeLabel?: string;
  /**
   * 宿主计算出的分数，显示在右侧。
   * Score rendered on the right when the host computes one.
   */
  scoreLabel?: string;
  /**
   * 悬停或键盘聚焦分数时展示的详情；仅在提供 `scoreLabel` 时显示。
   * Details shown on score hover or keyboard focus; requires `scoreLabel`.
   */
  scoreDetails?: ReactNode;
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

/** 通用文本结果。A general text result. */
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
  /**
   * 文本格式，缺省为 `plain`。
   * Text format; defaults to `plain`.
   */
  format?: 'plain' | 'markdown';
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

/**
 * 单个资源的预览结果，例如读取一个文件。
 *
 * A preview of one resource, for example reading a file.
 */
export interface TuiResourcePreviewContent {
  /**
   * 变体标记，固定为 `resource-preview`。
   * Variant discriminator, always `resource-preview`.
   */
  variant: 'resource-preview';
  /**
   * 资源标题。
   * Resource title.
   */
  title: string;
  /**
   * 正文，通常是文件内容。
   * Body, usually the file content.
   */
  content: string;
  /**
   * 标题下方的元信息，例如类型与大小，按顺序以分隔符连接展示。
   * Metadata under the title, for example type and size, joined in order.
   */
  meta?: readonly string[];
  /**
   * 没有 `meta` 时使用的单行说明。
   * Single-line description used when `meta` is absent.
   */
  description?: string;
  /**
   * 资源自身的 URI，显示为可点击入口。
   * The resource's own URI, rendered as an openable entry.
   */
  uri?: string;
  /**
   * 点击 URI 时跳转到的位置；省略时使用 `uri`。
   * Where clicking the URI navigates to; `uri` is used when omitted.
   */
  navigationUri?: string;
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

/**
 * 命令帮助结果：按分组列出可用命令。
 *
 * A command-help result: available commands listed in groups.
 */
export interface TuiCommandHelpContent {
  /**
   * 变体标记，固定为 `command-help`。
   * Variant discriminator, always `command-help`.
   */
  variant: 'command-help';
  /**
   * 命令分组。
   * Command groups.
   */
  sections: readonly TuiCommandHelpSection[];
}

/** 命令帮助中的一组命令。One group of commands in the help result. */
export interface TuiCommandHelpSection {
  /**
   * 分组唯一标识。
   * Unique section key.
   */
  key: string;
  /**
   * 分组标题。
   * Section title.
   */
  label: string;
  /**
   * 该组下的命令。
   * Commands in this group.
   */
  items: readonly TuiCommandHelpItem[];
}

/** 命令帮助中的一条命令。One command in the help result. */
export interface TuiCommandHelpItem {
  /**
   * 命令唯一标识。
   * Unique command key.
   */
  key: string;
  /**
   * 用于显示和填入的命令文本。
   * Command text used for display and insertion.
   */
  name: string;
  /**
   * 命令说明。
   * Command description.
   */
  description?: string;
}

/** 命令结果内容。Command result content. */
export type TuiRecordContent =
  | TuiTextContent
  | TuiSearchListContent
  | TuiResourcePreviewContent
  | TuiResourceTreeContent
  | TuiStatusContent
  | TuiWriteResultContent
  | TuiCommandHelpContent;

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
   * 当前记录的执行状态；不控制其他命令的提交。
   * Execution status of this record; does not control other submissions.
   */
  status: TuiRecordStatus;
  /**
   * 执行结果，进行中时省略。
   * Execution result, omitted while pending.
   */
  content?: TuiRecordContent;
  /**
   * 未提供有效 createdAtMs 时使用的展示时间，不解析为日期。
   * Display text used without a valid createdAtMs; never parsed as a date.
   */
  createdAt?: string;
  /**
   * 发送时的 Unix 毫秒时间戳，优先在客户端格式化为相对时间或本地日期。
   * Unix timestamp in milliseconds; preferred for relative time or local date display on the client.
   */
  createdAtMs?: number;
}

/**
 * 可替换的界面文案，默认跟随 locale。
 * Replaceable interface copy, following locale.
 */
export interface TuiLabels {
  /**
   * 输入框占位文本。
   * Input placeholder.
   */
  placeholder?: string;
  /**
   * `disabled` 为 `true` 时展示的不可用提示；文案本身不控制禁用状态。
   * Unavailable message shown when `disabled` is `true`; copy does not control
   * the disabled state.
   */
  unavailableMessage?: string;
  /**
   * 底部发送快捷键的说明文案。
   * Label for the send keyboard shortcut.
   */
  send?: string;
  /** 全部指令快捷键说明。Label for the all-commands shortcut. */
  allCommands?: string;
  /** 历史指令快捷键说明。Label for recalling commands from the current records. */
  historyCommands?: string;
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
   * 等待中的状态文本。
   * Text for the pending state.
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
   * 内置空态的标题。
   * Heading of the built-in empty state.
   */
  emptyTitle?: string;
  /**
   * 内置空态的说明。
   * Description of the built-in empty state.
   */
  emptyDescription?: string;
  /**
   * 快捷命令列表上方的引导文案。
   * Label above the quick command list.
   */
  quickStart?: string;
  /** 无返回结果时的提示。Message for an empty command result. */
  noResult?: string;
  /** 刚刚发送的时间文案。Time label for a just-sent record. */
  justNow?: string;
  /** 命令帮助的标题与操作提示。Command-help heading and hint. */
  availableCommands?: string;
  helpHint?: string;
  /** 参数帮助标题、示例与空参数提示。Parameter help headings and empty copy. */
  parameters?: string;
  examples?: string;
  noParameters?: string;
  /** 参数表列名与必填值。Parameter columns and required values. */
  parameter?: string;
  required?: string;
  description?: string;
  defaultValue?: string;
  yes?: string;
  no?: string;
  /** 资源列表分页。Resource list pagination. */
  loadMore?: string;
  /** 资源表格的名称列。Resource identity column heading. */
  resource?: string;
  /** 格式化已过去的整分钟。Formats elapsed whole minutes. */
  formatMinutesAgo?: (minutes: number) => string;
  /** 格式化已过去的整小时。Formats elapsed whole hours. */
  formatHoursAgo?: (hours: number) => string;
}

/**
 * 当前命令记录、结果与输入区的公开接口；会话、历史面板和外层工具栏由宿主组合。
 * 提供命令补全、参数提示与帮助。
 * Public interface for current command records, results and input. The host
 * composes session controls, history panels and surrounding toolbars separately.
 * Provides command completion, parameter hints and help.
 */
export interface TuiProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onCopy' | 'onSubmit'
> {
  /**
   * 当前展示的执行记录，按提交顺序排列；会话与历史切换后由宿主更新。
   * Currently displayed execution records in submission order, updated by the
   * host when switching sessions or historical results.
   */
  records: readonly TuiRecord[];
  /**
   * 受控的输入值。
   * Controlled input value.
   */
  value: string;
  /** 可选文案语言；继承上层，最终默认英文。Optional locale; inherits or defaults to en-US. */
  locale?: Locale;
  /**
   * 是否禁用命令输入与提交，缺省为 `false`。
   * 禁用时的提示文案由 `labels.unavailableMessage` 提供。
   * Whether command input and submission are disabled; defaults to `false`.
   * Disabled copy comes from `labels.unavailableMessage`.
   */
  disabled?: boolean;
  /**
   * 无记录时展示的空态节点，完全替换内置空态。
   * 省略时按 `quickCommands` 展示内置空态，没有可用快捷命令时不显示任何空态内容；
   * 传 `null` 时同样不显示。
   * Empty-state node shown when there are no records, replacing the built-in
   * empty state. When omitted, the built-in empty state renders the resolved
   * `quickCommands`; without any resolved command nothing is shown. Passing
   * `null` also shows nothing.
   */
  emptyState?: ReactNode;
  /**
   * 内置空态展示的快捷命令名称，按数组顺序取自 `commands`，未命中的名称跳过，
   * 重复名称只保留首次出现。
   * 点击后填入输入框并聚焦，不提交命令。
   * Quick command names rendered by the built-in empty state, resolved from
   * `commands` in array order; unknown names are skipped and repeats keep their
   * first occurrence. Selecting one fills
   * and focuses the input without submitting.
   */
  quickCommands?: readonly string[];
  /** 宿主提供的命令目录；省略时无候选和参数帮助，仍可提交文本。 */
  commands?: readonly TuiCommandDefinition[];
  /** 宿主提供的分组名称与顺序；未声明的组按目录顺序追加。 */
  commandGroups?: readonly TuiCommandGroup[];
  /**
   * 输入值变化时触发，包括接受命令或示例；行内占位不改变值，也不提交。省略时输入框
   * 只读，用于展示历史记录。
   * Invoked when the input changes, including accepted commands and examples; filling the input does not submit a command. Omission makes the input
   * read-only, for showing history.
   */
  onValueChange?: (value: string) => void;
  /**
   * 用户显式提交时上报命令意图，宿主负责请求、权限和执行，并通过 records 回填状态。
   * Reports an explicit submission intent. The host owns requests, permissions
   * and execution, and supplies subsequent status through records. Omission disables submission.
   */
  onSubmit?: (command: string) => void;
  /**
   * 点击资源 URI 时触发，由宿主接管打开；覆盖输入区、命令记录和结果中的链接。
   * Called when a resource URI is activated in the input, command echo or results;
   * the host owns navigation. Without a handler, text links use their native href.
   */
  onOpenUri?: (uri: string) => void;
  /**
   * 复制成功后触发，参数为已复制的文本；复制失败时不触发。
   * Called after copying succeeds, with the copied text; not called on failure.
   */
  onCopy?: (text: string) => void;
  /**
   * 统一的界面文案与引导提示；仅替换展示内容，不改变可用性。
   * Interface copy and guidance; replaces display content without changing
   * availability.
   */
  labels?: TuiLabels;
}
