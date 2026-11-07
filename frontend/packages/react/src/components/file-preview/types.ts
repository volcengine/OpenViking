import type {
  ConfirmSlotProps,
  DownloadSlotProps,
  FileViewerResource,
  ViewerHandle,
} from '@ark-design/ui/file-viewer/core';
import type {
  ComponentType,
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
   * Text content, for formats that can be inlined such as Markdown, JSON and code.
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
   * 完整路径，显示为面包屑。省略时面包屑退化为文件名，不渲染祖先按钮。
   * Full path, rendered as the breadcrumb. When omitted the breadcrumb falls
   * back to the file name and renders no ancestor buttons.
   */
  path?: string;
  /**
   * 显式格式提示，优先级高于扩展名与 MIME。
   * Explicit format hint, taking precedence over extension and MIME.
   */
  fileType?: string;
  /**
   * 正文是否被截断。宿主按读取上限取回部分内容时置为 `true`，组件在正文
   * 下方提示，避免把「只读了一部分」当作完整文件。
   * Whether the body was truncated. A host that read only part of the content
   * sets this so the component can say so instead of passing a partial file off
   * as complete.
   */
  truncated?: boolean;
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
   * What the tab row shows. It can be plain text or a node with styling or icons.
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
   * 祖先导航成功打开后触发，参数为被点击那一级的完整 URI；失败时不触发。
   * Called after an ancestor opens, with the full URI of the clicked level;
   * not called on failure.
   */
  onOpenParent?: (uri: string) => void;
  /**
   * 路径复制成功后触发，参数为已复制的路径；复制失败时不触发。
   * 省略回调不影响复制。文件页签提供 `source.path` 时显示复制路径按钮。
   * Called after path copying succeeds, with the copied path; not called on
   * failure. Omitting it does not affect copying. File tabs with `source.path`
   * show the copy-path button.
   */
  onCopyPath?: (path: string) => void;
  /**
   * 是否显示预览/源码切换，默认 `true`。格式本身没有源码视图时自动隐藏。
   * 只在文件页签上生效；内容页签的正文由宿主渲染，组件没有可切换的两种视图。
   * Whether to show the preview/source switch, `true` by default. Hidden
   * automatically when the format has no source view. Only meaningful on file
   * tabs; a content tab renders whatever views it needs inside `content`.
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
   * 文件操作的执行能力；省略对应方法时该操作不可用。
   * File operation capabilities; omitting a method makes its operation unavailable.
   */
  operations?: FilePreviewOperations;
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
   * Header slots of a file tab, adding the three state fallbacks on top of the base ones.
   */
  slots?: FilePreviewFileTabSlots;
  /**
   * 正文复制成功后触发，参数为已复制的文本；复制失败时不触发。
   * 省略回调不影响复制。仅文件页签提供正文复制，没有可复制文本时按钮禁用。
   * Called after content copying succeeds, with the copied text; not called on
   * failure. Omitting it does not affect copying. Only file tabs provide body
   * copying; the button is disabled when no copyable text is available.
   */
  onCopyContent?: (text: string) => void;
  /**
   * 下载操作成功后触发，失败时不触发。
   * Called after the download operation succeeds; not called on failure.
   */
  onDownload?: () => void;
  /**
   * 正文中的资源成功打开后触发，参数为已打开的 URI；失败时不触发。
   * Called after a content resource opens, with its URI; not called on failure.
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
   * 页签行显示的内容。内容页签没有 `source.fileName` 可以回落，因此这里是必填。
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
 * 文件操作的执行能力。方法返回的 Promise 完成后才触发对应的成功通知。
 * File operation capabilities. The matching success notification fires only
 * after the returned promise resolves.
 */
export interface FilePreviewOperations {
  /**
   * 打开给定的祖先 URI，参数可能指向任意一级目录，不限于当前文件的直接父级。
   * Opens the given ancestor URI. The argument may point at any level, not only
   * the immediate parent of the current file.
   */
  openParent?: (uri: string) => Promise<void>;
  /** 下载当前文件，完成后返回。Downloads the current file and resolves on completion. */
  download?: () => Promise<void>;
  /** 打开正文引用的资源。Opens a resource referenced by the content. */
  openUri?: (uri: string) => Promise<void>;
}

/**
 * 文件编辑配置。省略时只读；可提供编辑所需的完整文本，并处理保存与取消操作。
 * File-editing options. Omit them for a read-only preview. Provide the full text
 * to edit and handle save and cancel actions as needed.
 */
export interface FilePreviewEditing {
  /**
   * 进入编辑时读取完整文本，适用于截断预览或文件地址。
   * 省略时使用 `content` 或 `file` 中的文本；仅提供 `url` 时需提供此读取函数。
   * 返回 `undefined` 时放弃本次编辑，保持预览态。
   * Provides the full text when editing is requested, for example for a truncated
   * preview or a file URL. When omitted, text from `content` or `file` is used;
   * URL-only sources require this loader. Returning `undefined` cancels the edit
   * request and keeps the preview open.
   */
  loadContent?: (source: FilePreviewSource) => Promise<string | undefined>;
  /**
   * 保存草稿时调用。抛错或 reject 时组件展示失败提示并保留草稿。
   * Invoked when the draft is saved. A thrown error or rejection keeps the draft
   * and shows a failure message.
   */
  save: (value: string) => Promise<void>;
  /**
   * 保存成功后触发，参数为已保存的文本；失败时不触发。
   * Called after saving succeeds, with the saved text; not called on failure.
   */
  onSave?: (value: string) => void;
  /**
   * 取消编辑或切换页签并丢弃草稿后触发，参数为丢弃的文本；保存成功时不触发。
   * Called after cancelling editing or switching tabs discards the draft;
   * receives the discarded text and is not called after a successful save.
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
   * 复制与下载使用已有按钮，不需要在这里重复添加。
   * Extra actions on the right of the header, for example permissions or delete.
   * Use the existing copy and download buttons instead of adding duplicates here.
   */
  actions?: ReactNode;
  /**
   * 标题旁的额外内容，例如权限标记、状态徽标或版本号；不需要时不传。
   * Extra content next to the title, for example a permission tag, a status badge
   * or a version label; omit it when not needed.
   */
  titleExtra?: ReactNode;
  /**
   * 正文下方的补充信息，例如字符数、更新时间或保留策略控件。渲染在正文与
   * 滚动容器之外，不随正文滚动。
   * Supplementary information under the body, for example a character count, an
   * update time or retention controls. Rendered outside the scrolling body.
   */
  footer?: ReactNode;
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
  /**
   * 大文件确认态的替代内容。需要拿到确认与取消回调，因此是组件而不是节点。
   * 不传时使用内置确认态与底座文案。
   * Replacement for the large-file confirmation state. It receives the confirm
   * and cancel callbacks, so it is a component rather than a node. A built-in
   * state with base copy is used when omitted.
   */
  confirmFallback?: ComponentType<ConfirmSlotProps>;
  /**
   * 无法内联预览时的替代内容，通常包含下载入口。需要拿到下载与重试回调，
   * 因此是组件而不是节点。不传时使用内置提示与底座文案。
   * Replacement for the state where inline preview is impossible, usually with a
   * download entry. It receives the download and retry callbacks, so it is a
   * component rather than a node. A built-in message with base copy is used when
   * omitted.
   */
  downloadFallback?: ComponentType<DownloadSlotProps>;
}

/**
 * 可替换文案，组件内置英文默认值。
 * Replaceable copy; the component ships English defaults.
 */
export interface FilePreviewLabels {
  /** 复制内容按钮的文案。Label of the copy-content button. */
  copyContent?: string;
  /** 复制内容成功后的提示。Message shown after the content was copied. */
  copyContentSuccess?: string;
  /** 没有内容可复制时的提示。Message shown when there is no content to copy. */
  copyContentEmpty?: string;
  /** 复制路径按钮的文案。Label of the copy-path button. */
  copyPath?: string;
  /** 复制路径成功后的提示。Message shown after the path was copied. */
  copyPathSuccess?: string;
  /** 打开上级目录的按钮文案。Label of the control that opens an ancestor. */
  openParent?: string;
  /** 进入编辑状态的按钮文案。Label of the control that enters editing. */
  edit?: string;
  /** 保存按钮文案。Save button label. */
  save?: string;
  /** 取消编辑的按钮文案。Label of the control that leaves editing. */
  cancel?: string;
  /** 预览视图的文案。Label of the preview view. */
  preview?: string;
  /** 源码视图的文案。Label of the source view. */
  source?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
  /** 读取失败时的提示。Message shown when reading failed. */
  loadFailed?: string;
  /** 保存失败时的提示。Message shown when saving failed. */
  saveFailed?: string;
  /** 无法预览该格式时的提示。Message shown when the format cannot be previewed. */
  unsupported?: string;
  /** 取回正文时的加载提示。Message shown while the text is being fetched. */
  loading?: string;
  /** 下载按钮文案。Label of the download button. */
  download?: string;
  /** 无法预览二进制文件时的提示。Message shown when a binary file cannot be previewed. */
  binaryUnsupported?: string;
  /** 文件长度为 0 时的提示。Message shown when the file itself is empty. */
  fileEmpty?: string;
  /** 文件非空但没有可展示内容时的提示。Message shown for a non-empty file with nothing to render. */
  noContent?: string;
  /** 正文被截断时的提示。Message shown when the body was truncated. */
  truncated?: string;
}

/**
 * 通过组件 ref 切换或查询当前页签，并透传底座的命令式能力。
 * Switches or queries the active tab through the component ref, and exposes the
 * base component's imperative capabilities.
 */
export interface FilePreviewHandle {
  /**
   * 切到指定 key 的页签。该页签不存在或被禁用时返回 `false`。
   * Switches to the tab with the given key. Returns `false` when the tab does not
   * exist or is disabled.
   */
  setActiveTab: (key: string) => boolean;
  /** 取当前激活页签的 key。Returns the key of the active tab. */
  getActiveTab: () => string;
  /**
   * 取当前文件页签的底座句柄，用于翻页、搜索、缩放等命令式操作。
   * 当前页签不是文件页签、或底座尚未就绪时返回 `null`。
   * Returns the base handle of the active file tab for page, search and zoom
   * commands. Returns `null` when the active tab is a content tab or the base is
   * not ready yet.
   */
  getViewerHandle: () => ViewerHandle | null;
}

/**
 * 文件预览配置，支持文件页签与自定义内容页签。
 * 通过页签配置内容与操作，文件页签可提供编辑能力与状态提示。
 * File-preview options supporting file tabs and custom-content tabs. Configure
 * content and actions per tab; file tabs also support editing options and status
 * content.
 */
export interface FilePreviewProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'children'
> {
  /**
   * 页签列表，至少一个。每个页签用 `source` 交给组件按格式渲染，或用 `content`
   * 完全自定义渲染。
   * The tab list, at least one entry. Each tab either hands `source` to the
   * component for format rendering or renders fully custom `content`.
   */
  tabs: readonly [FilePreviewTab, ...FilePreviewTab[]];
  /**
   * 受控的当前页签 key。传入时由宿主决定当前页签，组件只上报切换意图。
   * The controlled active tab key. When given, the host owns the active tab and
   * the component only reports switch intent.
   */
  activeKey?: string;
  /**
   * 非受控时的初始页签 key，省略时选中第一个。
   * Initial tab key when uncontrolled; the first tab is selected when omitted.
   */
  defaultActiveKey?: string;
  /**
   * 底座内置文案的语言。组件自身文案仍由 `labels` 控制。
   * Language of the base component's built-in copy. The component's own copy is
   * still controlled by `labels`.
   */
  locale?: 'en-US' | 'zh-CN';
  /**
   * 当前页签变化后触发，参数为新的页签 key。
   * Called after the active tab changes, with the new tab key.
   */
  onActiveKeyChange?: (key: string) => void;
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

/**
 * 文件页签的视图模式。底座的 `ViewerViewMode` 是不透明的字符串，这里只收
 * 组件自己会切换的两种；其它格式的私有模式（Word 版式/阅读、CAD 2D/3D）
 * 由底座与 Adapter 自行管理，不经过组件。
 *
 * View modes of a file tab. The base `ViewerViewMode` is an opaque string; only
 * the two the component switches between are listed here. Format-private modes
 * (Word layout/reading, CAD 2D/3D) are managed by the base and its adapters.
 */
export type FilePreviewViewMode = 'preview' | 'source';
