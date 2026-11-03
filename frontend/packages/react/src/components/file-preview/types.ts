import type { HTMLAttributes, ReactNode } from 'react';

/**
 * 预览当前展示的内容种类，由宿主决定使用哪一种。
 * What the preview is currently showing; the host decides which one applies.
 */
export type FilePreviewContentKind =
  /**
   * 未选中文件。
   * No file selected.
   */
  | 'empty'
  /**
   * 内容加载中。
   * Content is loading.
   */
  | 'loading'
  /**
   * 内容加载失败。
   * Content failed to load.
   */
  | 'error'
  /**
   * 纯文本。
   * Plain text.
   */
  | 'text'
  /**
   * Markdown 渲染。
   * Rendered Markdown.
   */
  | 'markdown'
  /**
   * 格式化后的 JSON。
   * Formatted JSON.
   */
  | 'json'
  /**
   * JSONL 逐行记录，例如会话消息。
   * Line-delimited JSON records, for example session messages.
   */
  | 'jsonl'
  /**
   * HTML 渲染。
   * Rendered HTML.
   */
  | 'html'
  /**
   * 图片。
   * Image.
   */
  | 'image'
  /**
   * 二进制文件，通常只展示下载入口。
   * Binary file, usually only a download action.
   */
  | 'binary'
  /**
   * 目录摘要。
   * Directory summary.
   */
  | 'directory';

/**
 * 预览所描述的文件或目录，不含内容本身。
 * A file or directory the preview can describe, without its content.
 */
export interface FilePreviewTarget {
  /**
   * 资源标识，通过回调原样回传。
   * Resource identifier reported back through callbacks unchanged.
   */
  uri: string;
  /**
   * 显示名称。
   * Display name.
   */
  name: string;
  /**
   * 目标类型：目录或文件。
   * Target type: directory or file.
   */
  type:
    /**
     * 目录。
     * Directory.
     */
    | 'directory'
    /**
     * 文件。
     * File.
     */
    | 'file';
  /**
   * 小写扩展名，不含点号，用于选择渲染器。
   * Lowercase extension without the dot, used to pick a renderer.
   */
  extension?: string;
  /**
   * MIME 类型，未知时省略。
   * MIME type, omitted when unknown.
   */
  mimeType?: string;
  /**
   * 字节大小，未知时为 `null`。
   * Size in bytes, or `null` when unknown.
   */
  sizeBytes?: number | null;
  /**
   * 更新时间，未知时为 `null`。
   * Last update time, or `null` when unknown.
   */
  updatedAt?: string | null;
  /**
   * 显示在标题下方的简短摘要。
   * Short summary shown under the name in the header.
   */
  abstract?: string;
}

/**
 * 宿主已准备好的内容，预览自身不读取文件。
 * Content prepared by the host; the preview never reads it itself.
 */
export interface FilePreviewContent {
  /**
   * 内容种类，决定使用哪个渲染器。
   * Content kind, which selects the renderer.
   */
  kind: FilePreviewContentKind;
  /**
   * `text`、`markdown`、`json`、`jsonl`、`html` 的文本正文。
   * Text body for `text`, `markdown`, `json`, `jsonl` and `html`.
   */
  text?: string;
  /**
   * `image` 的媒体地址。
   * Media source for `image`.
   */
  src?: string;
  /**
   * `error` 的失败原因；读取失败时同样使用该字段。
   * Failure reason for `error`; also used when a read failed.
   */
  errorMessage?: string;
  /**
   * 本次读取只返回了前缀，宿主可以继续加载。
   * The read returned a prefix only; the host can load more.
   */
  truncated?: boolean;
  /**
   * `text` 对应的字节偏移，用于续读。
   * Byte offset of `text`, used when continuing a truncated read.
   */
  offset?: number;
  /**
   * `text` 中已加载的字节数。
   * Number of bytes returned in `text`.
   */
  loadedBytes?: number;
  /**
   * 文件总字节数，未知时为 `null`。
   * Total size in bytes, or `null` when unknown.
   */
  totalBytes?: number | null;
}

/**
 * 编辑态；不传即为只读预览。
 * Editable state; omit entirely for a read-only preview.
 */
export interface FilePreviewEditing {
  /**
   * 宿主持有的当前草稿值。
   * Current draft text owned by the host.
   */
  value: string;
  /**
   * 草稿与已保存内容是否不一致。
   * Whether the draft differs from the saved content.
   */
  dirty?: boolean;
  /**
   * 是否正在保存。
   * Whether a save is in flight.
   */
  saving?: boolean;
  /**
   * 上次保存失败时的提示。
   * Shown when the last save failed.
   */
  errorMessage?: string;
  /**
   * 草稿变化时触发。
   * Invoked when the draft changes.
   */
  onChange: (value: string) => void;
  /**
   * 请求保存当前草稿。
   * Requests saving the current draft.
   */
  onSave: () => void;
  /**
   * 请求放弃当前草稿。
   * Requests discarding the current draft.
   */
  onDiscard: () => void;
}

/**
 * 目录预览上方的一个 L0/L1 摘要页签。
 * One L0/L1 style summary tab above a directory preview.
 */
export interface FilePreviewSummaryTab {
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
   * 页签内容，由宿主渲染。
   * Tab content, rendered by the host.
   */
  content: ReactNode;
  /**
   * 该页签内容是否正在加载。
   * Whether this tab's content is loading.
   */
  loading?: boolean;
  /**
   * 该页签内容加载失败时的提示。
   * Shown when this tab's content failed to load.
   */
  errorMessage?: string;
}

/**
 * 宿主提供的额外页签，例如经验看板。
 * A host-provided extra tab, for example the experience dashboard.
 */
export interface FilePreviewExtraTab {
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
   * 页签图标。
   * Tab icon.
   */
  icon?: ReactNode;
  /**
   * 页签内容，由宿主渲染。
   * Tab content, rendered by the host.
   */
  content: ReactNode;
  /**
   * 是否禁用该页签。
   * Whether the tab is disabled.
   */
  disabled?: boolean;
}

/**
 * 可替换文案，组件内置英文默认值。
 * Replaceable copy; the component ships English defaults.
 */
export interface FilePreviewLabels {
  /**
   * 关闭按钮的无障碍名称。
   * Accessible name of the close control.
   */
  close?: string;
  /**
   * 复制按钮文案。
   * Copy button label.
   */
  copy?: string;
  /**
   * 复制成功后的提示。
   * Message shown after a successful copy.
   */
  copied?: string;
  /**
   * 下载按钮文案。
   * Download button label.
   */
  download?: string;
  /**
   * 进入编辑状态的按钮文案。
   * Label of the control that enters editing.
   */
  edit?: string;
  /**
   * 保存按钮文案。
   * Save button label.
   */
  save?: string;
  /**
   * 放弃草稿按钮文案。
   * Discard-draft button label.
   */
  discard?: string;
  /**
   * 取消按钮文案。
   * Cancel button label.
   */
  cancel?: string;
  /**
   * 重试按钮文案。
   * Retry button label.
   */
  retry?: string;
  /**
   * 继续加载按钮文案。
   * Load-more button label.
   */
  loadMore?: string;
  /**
   * 内容被截断时的提示。
   * Message shown when the content is truncated.
   */
  truncated?: string;
  /**
   * 无法预览二进制文件时的提示。
   * Message shown when a binary file cannot be previewed.
   */
  binaryUnsupported?: string;
  /**
   * 未选中文件时的提示。
   * Message shown when no file is selected.
   */
  empty?: string;
  /**
   * 打开上级目录的按钮文案。
   * Label of the control that opens the parent directory.
   */
  openParent?: string;
  /**
   * 更多操作的无障碍名称。
   * Accessible name of the more-actions control.
   */
  actions?: string;
}

/** 文件预览的公开接口。Public interface of the file preview. */
export interface FilePreviewProps extends Omit<
  HTMLAttributes<HTMLElement>,
  'content' | 'onChange' | 'onCopy'
> {
  /**
   * 当前选中的文件或目录；为 `null` 时渲染空态。
   * The selected file or directory; `null` renders the empty state.
   */
  target?: FilePreviewTarget | null;
  /**
   * 宿主准备好的内容。
   * Content prepared by the host.
   */
  content?: FilePreviewContent;
  /**
   * 目录的摘要页签；对文件无效。
   * Summary tabs for a directory; ignored for files.
   */
  summaryTabs?: readonly FilePreviewSummaryTab[];
  /**
   * 宿主自有的页签，追加在内置页签之后。
   * Host-owned tabs appended after the built-in ones.
   */
  extraTabs?: readonly FilePreviewExtraTab[];
  /**
   * 编辑态；不传即为只读。
   * Editing state; omit for a read-only preview.
   */
  editing?: FilePreviewEditing;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: FilePreviewLabels;
  /**
   * 宿主提供的标题栏动作，例如权限设置或删除。
   * Header actions supplied by the host, such as permissions or delete.
   */
  actions?: ReactNode;
  /**
   * 显示在标题旁的权限或状态标记。
   * Permission or status tag shown next to the title.
   */
  metaTag?: ReactNode;
  /**
   * 是否显示关闭按钮。
   * Whether to render the close button.
   */
  showCloseButton?: boolean;
  /**
   * 宿主能提供下载地址时显示下载动作。
   * Renders the download action when the host can provide a URL.
   */
  canDownload?: boolean;
  /**
   * 替换整个内容区，用于宿主自定义渲染。
   * Replaces the whole body for host-specific rendering.
   */
  children?: ReactNode;
  /**
   * 请求关闭预览。
   * Requests closing the preview.
   */
  onClose?: () => void;
  /**
   * 请求下载当前文件。
   * Requests downloading the current file.
   */
  onDownload?: () => void;
  /**
   * 跳转到内容中出现的 URI。
   * Navigates to a URI found in the rendered content.
   */
  onNavigate?: (uri: string) => void;
  /**
   * 跳转到上级目录。
   * Navigates to the parent directory.
   */
  onOpenParent?: (uri: string) => void;
  /**
   * 重新加载当前内容。
   * Reloads the current content.
   */
  onRetry?: () => void;
  /**
   * 加载被截断内容的下一段。
   * Loads the next slice of a truncated read.
   */
  onLoadMore?: () => void;
  /**
   * 复制由宿主执行，预览只上报意图。
   * The host performs the copy; the preview only reports the intent.
   */
  onCopy?: (text: string) => void;
}
