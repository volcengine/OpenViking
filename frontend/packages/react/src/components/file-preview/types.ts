import type { FileViewerResource } from '@ark-design/ui/file-viewer/core';
import type {
  ForwardRefExoticComponent,
  HTMLAttributes,
  PropsWithoutRef,
  ReactNode,
  RefAttributes,
} from 'react';

/**
 * 要预览的内容及文件信息。提供多个来源时，按 `content`、`file`、`url`
 * 的顺序使用第一个有值的来源。
 * The content and file information to preview. When multiple sources are
 * provided, the first available one is used in the order `content`, `file`, `url`.
 */
export interface FilePreviewSource {
  /**
   * 文本内容，适用于 Markdown、JSON、代码等可内联的格式。
   * Text content, for formats that can be inlined such as Markdown, JSON and
   * code.
   */
  content?: string;
  /**
   * 二进制文件，适用于 PDF、Office 文档、表格与 3D 模型等。
   * Binary file, for PDF, Office documents, spreadsheets and 3D models.
   */
  file?: File | Blob;
  /**
   * 文件地址。宿主已经能提供可访问链接时使用，组件不自行鉴权。
   * File URL. Used when the host can already provide an accessible link; the
   * component performs no authentication of its own.
   */
  url?: string;
  /**
   * 文件名，用于识别格式与展示标题。无扩展名的文件按文件名匹配，
   * 例如 `Dockerfile` 与 `Makefile`。
   * File name, used for format detection and the header title. Extensionless
   * files match by name, for example `Dockerfile` and `Makefile`.
   */
  fileName: string;
  /**
   * 完整路径，显示在标题下方。省略时只显示文件名。
   * Full path shown under the title. Only the file name is shown when omitted.
   */
  path?: string;
  /**
   * 显式格式提示，优先级高于扩展名与 MIME。
   * Explicit format hint, taking precedence over extension and MIME.
   */
  fileType?: string;
}

/**
 * 所有页签的公共配置，包括标识、标题、图标、文案及标题行操作。
 * Configuration shared by every tab, including identity, title, icon, copy and
 * header actions.
 */
export interface FilePreviewTabBase {
  /**
   * 页签唯一标识。
   * Unique tab key.
   */
  key: string;
  /**
   * 页签行显示的内容。可以是纯文本，也可以是带样式或图标的节点。
   * What the tab row shows. It can be plain text or a node with styling or
   * icons.
   */
  title?: ReactNode;
  /**
   * 页签图标。省略时按文件格式自动选择，内容页签使用内置的看板图标。
   * Tab icon. Chosen from the file format when omitted; a content tab uses the
   * built-in dashboard icon.
   */
  icon?: ReactNode;
  /**
   * 该页签的文案，逐项覆盖外层 `labels`。未提供的项回落到外层。
   * Copy of this tab, overriding the outer `labels` field by field. Fields left
   * out fall back to the outer ones.
   */
  labels?: Partial<FilePreviewLabels>;
  /**
   * 是否禁用该页签。
   * Whether the tab is disabled.
   */
  disabled?: boolean;
  /**
   * 请求打开上级目录，参数为当前文件的 `source.path`。
   * 仅在文件页签提供路径时生效；省略时路径不可点击。
   * Requests opening the parent directory, passing the current file's
   * `source.path`. Applies only to file tabs with a path; omitting it makes
   * the path non-interactive.
   */
  onOpenParent?: (path: string) => void;
  /**
   * 复制当前节点路径。取值来自文件页签的 `source.path`；内容页签没有路径，
   * 传了也不会渲染按钮。
   *
   * Copies the current node path. The value comes from a file tab's
   * `source.path`; a content tab has no path, so passing it renders nothing.
   */
  onCopyPath?: (path: string) => void;
  /**
   * 是否显示预览/源码切换，默认 `true`。格式本身没有源码视图时自动隐藏。
   *
   * 只在文件页签上生效。内容页签的正文由宿主渲染，组件没有可切换的两种视图，
   * 传了也不会渲染切换按钮；宿主需要两种视图时在 `content` 里自行实现。
   *
   * Whether to show the preview/source switch, `true` by default. Hidden
   * automatically when the format has no source view.
   *
   * Only meaningful on file tabs. A content tab's body is rendered by the host,
   * so the component has no second view to switch to and renders no control;
   * a host that needs two views implements the switch inside `content`.
   */
  showViewModeSwitch?: boolean;
  /**
   * 内容区标题行的插槽。页签行本身只显示标题与图标，其余元素都在这一层。
   * Slots of the content header row. The tab row itself shows only a title and
   * an icon; everything else renders in this layer.
   */
  slots?: FilePreviewHeaderSlots;
}

/**
 * 文件预览页签或宿主自定义内容页签。
 * A file-preview tab or a tab with host-provided content.
 */
export type FilePreviewTab = FilePreviewFileTab | FilePreviewContentTab;

/**
 * 按文件格式预览 `source` 的页签。
 * A tab that previews `source` according to its file format.
 */
export interface FilePreviewFileTab extends FilePreviewTabBase {
  /**
   * 页签类型标记，固定为 `file`。
   * Tab kind discriminator, always `file`.
   */
  kind: 'file';
  /**
   * 要预览的内容及文件信息。
   * The content and file information to preview.
   */
  source: FilePreviewSource;
  /**
   * 主文件之外、按 key 读取的伴随资源，例如 3D 模型的贴图。
   * Companion resources read by key alongside the main file, for example
   * textures of a 3D model.
   */
  resources?: readonly FileViewerResource[];
  /**
   * 文件编辑配置；省略时只读。
   * File-editing options; omit them for a read-only preview.
   */
  editing?: FilePreviewEditing;
  /**
   * 文件页签的标题行插槽，在基类之上增加三类状态兜底。
   * Header slots of a file tab, adding the three state fallbacks on top of the
   * base ones.
   */
  slots?: FilePreviewFileTabSlots;
  /**
   * 复制正文内容。取值来自 `source`（`content` 或 `file` 的文本），不传时
   * 不显示该按钮。
   *
   * 内容页签的正文由宿主渲染，组件拿不到纯文本，因此这个回调只在文件页签上
   * 有效；内容页签需要复制时由宿主在 `content` 里自行实现。
   *
   * Copies the body content. The value comes from `source` (the text of
   * `content` or `file`); no such button is rendered when omitted.
   *
   * A content tab's body is rendered by the host and the component has no plain
   * text for it, so this only applies to file tabs. Implement copying inside
   * `content` when a content tab needs it.
   */
  onCopyContent?: (text: string) => void;
  /**
   * 请求下载当前文件。不传时不显示下载按钮。
   * Requests downloading this file. No download button is rendered when
   * omitted.
   */
  onDownload?: () => void;
  /**
   * 跳转到内容中出现的资源 URI。
   * Navigates to a resource URI found in the rendered content.
   */
  onOpenUri?: (uri: string) => void;
}

/**
 * 展示宿主自定义内容的页签，例如看板或图谱。
 * 标题行可通过 `slots` 补充内容与操作；正文及其加载、错误提示由 `content` 提供。
 * A tab with host-provided content, such as a dashboard or graph. Use `slots`
 * for extra header content and actions, and `content` for the body and any
 * loading or error messages.
 */
export interface FilePreviewContentTab extends FilePreviewTabBase {
  /**
   * 页签类型标记，固定为 `content`。
   * Tab kind discriminator, always `content`.
   */
  kind: 'content';
  /**
   * 页签行显示的内容。内容页签没有 `source.fileName` 可以回落，因此这里是
   * 必填。
   * What the tab row shows. A content tab has no `source.fileName` to fall back
   * to, so it is required here.
   */
  title: ReactNode;
  /**
   * 页签正文，由宿主渲染。
   * The tab body, rendered by the host.
   */
  content: ReactNode;
}

/**
 * 文件编辑配置。省略时只读；可提供编辑所需的完整文本，并处理保存与取消操作。
 * File-editing options. Omit them for a read-only preview. Provide the full
 * text to edit and handle save and cancel actions as needed.
 */
export interface FilePreviewEditing {
  /**
   * 用户请求编辑时提供完整文本，适用于预览内容被截断或仅提供文件地址的场景。
   * 省略时使用 `content` 或 `file` 中的文本；仅提供 `url` 时需通过此回调提供文本。
   * 返回 `undefined` 时放弃本次编辑，保持预览态。
   * Provides the full text when editing is requested, for example for a truncated
   * preview or a file URL. When omitted, text from `content` or `file` is used;
   * URL-only sources require this callback. Returning `undefined` cancels the
   * edit request and keeps the preview open.
   */
  onRequestEdit?: (
    source: FilePreviewSource,
  ) => Promise<string | undefined> | string | undefined;
  /**
   * 保存草稿时调用。抛错或 reject 时组件展示失败提示并保留草稿。
   * Invoked when the draft is saved. A thrown error or rejection keeps the
   * draft and shows a failure message.
   */
  onSave: (value: string) => Promise<void> | void;
  /**
   * 退出编辑且草稿被丢弃时调用，参数是丢弃的草稿内容。
   * 用户点取消、切换到其他页签、关闭预览都会触发；保存成功不触发。
   * Invoked when editing ends and the draft is discarded, passing the discarded
   * draft. Triggered by cancellation, switching tabs or closing the preview;
   * not triggered by a successful save.
   */
  onCancel?: (value: string) => void;
}

/**
 * 所有页签共用的内容区标题行插槽。
 * Content-header slots shared by every tab.
 */
export interface FilePreviewHeaderSlots {
  /**
   * 标题行右侧的额外动作，例如权限设置或删除。
   *
   * 复制与下载不从这里放——它们由 `onCopyContent`、`onCopyPath` 与
   * `onDownload` 控制内置按钮，避免同一入口出现两个按钮。
   *
   * Extra actions on the right of the header, for example permissions or
   * delete.
   *
   * Copy and download do not belong here — the built-in buttons are driven by
   * `onCopyContent`, `onCopyPath` and `onDownload`, so the same entry never
   * shows twice.
   */
  actions?: ReactNode;
  /**
   * 标题旁的额外内容。宿主想放在文件名之后的任何东西都可以从这里注入，
   * 例如权限标记、状态徽标或版本号；不需要时不传。
   * Extra content next to the title. Anything the host wants after the file
   * name can go here, for example a permission tag, a status badge or a
   * version label; omit it when not needed.
   */
  titleExtra?: ReactNode;
}

/**
 * 文件页签的标题行插槽，以及加载、失败或格式不支持时的替代内容。
 * Header slots of a file tab and replacement content for loading, failure or
 * unsupported formats.
 */
export interface FilePreviewFileTabSlots extends FilePreviewHeaderSlots {
  /**
   * 内容读取中的兜底内容。不传时使用内置加载态。
   * Fallback while the content is being read. A built-in loading state is used
   * when omitted.
   */
  loadingFallback?: ReactNode;
  /**
   * 读取失败时的兜底内容。不传时使用内置错误态与重试入口。
   * Fallback when reading failed. A built-in error state with a retry entry is
   * used when omitted.
   */
  errorFallback?: ReactNode;
  /**
   * 格式不支持时的兜底内容。不传时使用内置提示。
   * Fallback when the format cannot be previewed. A built-in message is used
   * when omitted.
   */
  unsupportedFallback?: ReactNode;
}

/**
 * 可替换文案，组件内置英文默认值。
 * Replaceable copy; the component ships English defaults.
 */
export interface FilePreviewLabels {
  /**
   * 复制内容按钮的文案。
   * Label of the copy-content button.
   */
  copyContent?: string;
  /**
   * 复制内容成功后的提示。
   * Message shown after the content was copied.
   */
  copyContentSuccess?: string;
  /**
   * 没有内容可复制时的提示。
   * Message shown when there is no content to copy.
   */
  copyContentEmpty?: string;
  /**
   * 复制路径按钮的文案。
   * Label of the copy-path button.
   */
  copyPath?: string;
  /**
   * 复制路径成功后的提示。
   * Message shown after the path was copied.
   */
  copyPathSuccess?: string;
  /**
   * 打开上级目录的按钮文案。
   * Label of the control that opens the parent directory.
   */
  openParent?: string;
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
   * 取消编辑的按钮文案。
   * Label of the control that leaves editing.
   */
  cancel?: string;
  /**
   * 预览视图的文案。
   * Label of the preview view.
   */
  preview?: string;
  /**
   * 源码视图的文案。
   * Label of the source view.
   */
  source?: string;
  /**
   * 重试按钮文案。
   * Retry button label.
   */
  retry?: string;
  /**
   * 读取失败时的提示。
   * Message shown when reading failed.
   */
  loadFailed?: string;
  /**
   * 保存失败时的提示。
   * Message shown when saving failed.
   */
  saveFailed?: string;
  /**
   * 无法预览该格式时的提示。
   * Message shown when the format cannot be previewed.
   */
  unsupported?: string;
  /**
   * 取回正文时的加载提示。
   * Message shown while the text is being fetched.
   */
  loading?: string;
  /**
   * 下载按钮文案。
   * Label of the download button.
   */
  download?: string;
  /**
   * 无法预览二进制文件时的提示。
   * Message shown when a binary file cannot be previewed.
   */
  binaryUnsupported?: string;
}

/**
 * 通过组件 ref 切换或查询当前页签。
 * Switches or queries the active tab through the component ref.
 */
export interface FilePreviewHandle {
  /**
   * 切到指定 key 的页签。该页签不存在或被禁用时返回 `false`。
   * Switches to the tab with the given key. Returns `false` when the tab does
   * not exist or is disabled.
   */
  setActiveTab: (key: string) => boolean;
  /**
   * 取当前激活页签的 key。
   * Returns the key of the active tab.
   */
  getActiveTab: () => string;
}

/**
 * 文件预览配置，支持文件页签与自定义内容页签。
 * 通过页签配置内容与操作，文件页签可提供编辑能力与状态提示。
 * File-preview options supporting file tabs and custom-content tabs.
 * Configure content and actions per tab; file tabs also support editing options
 * and status content.
 */
export interface FilePreviewProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'children'
> {
  /**
   * 页签列表，至少一个。每个页签用 `source` 交给组件按格式渲染，或用
   * `content` 完全自定义渲染。
   * The tab list, at least one entry. Each tab either hands `source` to the
   * component for format rendering or renders fully custom `content`.
   */
  tabs: readonly [FilePreviewTab, ...FilePreviewTab[]];
  /**
   * 所有页签的默认文案，缺省为英文。页签可以逐项覆盖。
   * Default copy for every tab, English by default. A tab can override it field
   * by field.
   */
  labels?: FilePreviewLabels;
}

/**
 * 文件预览组件，可通过 ref 切换或查询当前页签。
 * A file-preview component whose ref supports switching and querying tabs.
 */
export type FilePreviewComponent = ForwardRefExoticComponent<
  PropsWithoutRef<FilePreviewProps> & RefAttributes<FilePreviewHandle>
>;
