import type { Locale } from '../../shared/locale.js';
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
   * 页签图标，由宿主提供；省略时该页签只显示标题，不占图标位。
   *
   * 组件不按文件名推断类型：格式识别属于上层职责，内置图标集会把整包图标
   * 拖进产物。需要按格式取图标的宿主可从 `@openviking/react/icons` 引入
   * `getFileTypeIcon`，把结果传进来。
   *
   * The tab icon, supplied by the host. Omit it and the tab shows only its title,
   * with no icon slot.
   *
   * The component does not infer a type from the file name: format detection
   * belongs to the caller, and bundling the icon set would pull every icon into
   * the output. A host that wants a per-format icon can import
   * `getFileTypeIcon` from `@openviking/react/icons` and pass the result in.
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
}

/**
 * 文件预览页签或宿主自定义内容页签。
 * A file-preview tab or a tab with host-provided content.
 */
export type FilePreviewTab =
  FilePreviewFileTab | FilePreviewContentTab | FilePreviewDirectoryTab;

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
   * 正文是否还在读取。为真时组件渲染加载态而不是正文，外壳（页签行、路径行）
   * 照常显示——宿主切换文件时外壳因此不闪，只有正文区在等。
   *
   * `source` 在加载中可以为空；组件不会把它判成「空文件」。
   *
   * Whether the body is still being read. When true the component renders its
   * loading state instead of the body while the shell — tab row, path row — stays
   * put, so a host switching files does not flash the whole area. `source` may be
   * empty while loading; it is not treated as an empty file.
   */
  loading?: boolean;
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
  /** 可选的分级内容，宿主提供 L0/L1/L2 等视图与内容。 Optional host-supplied content levels. */
  levels?: readonly FilePreviewLevel[];
  /** 受控分级 key。 Controlled content-level key. */
  activeLevelKey?: string;
  /** Markdown 图片地址解析；宿主负责相对路径、鉴权与 URL 生命周期。
   * Resolves Markdown images; the host owns relative paths, authentication and URL lifetime. */
  resolveImage?: (src: string, source: FilePreviewSource) => Promise<string>;
  /** 分级切换意图；受控时宿主更新 activeLevelKey。 Content-level selection intent. */
  onLevelChange?: (key: string) => void;
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
   * 正文中的链接成功打开后触发，参数为被点击的 URI；失败时不触发。只有
   * Markdown 正文会触发。
   *
   * Called after a link inside the body opens, with the clicked URI; not called on
   * failure. Only Markdown bodies fire this.
   */
  onOpenUri?: (uri: string) => void;
}

/** 一个资源的内容层级，使用独立 source 与资源表。 One content level with its own source and resources. */
export interface FilePreviewLevel {
  key: string;
  label: ReactNode;
  source: FilePreviewSource;
  resources?: readonly FileViewerResource[];
  disabled?: boolean;
  /** 摘要默认只读；只有明确提供此能力时才可编辑。 Summaries are read-only unless explicitly editable. */
  editing?: FilePreviewEditing;
}

/**
 * 展示宿主自定义内容的页签，例如看板或图谱。
 *
 * 这类页签只有页签行由组件负责，正文整块交给宿主——组件不渲染内容区标题行，
 * 也不在中间套任何容器。宿主想加工具栏、标题或状态提示，都写在自己的
 * `content` 里，按需要怎么排就怎么排。
 *
 * A tab with host-provided content, such as a dashboard or graph.
 *
 * The component only renders the tab row for these; the whole body belongs to the
 * host. No content header row is rendered and no wrapper is placed in between, so
 * a host that wants a toolbar, a title or a status line puts it inside `content`
 * and lays it out however it needs.
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
   * 页签正文，由宿主渲染，铺满页签行以下的整个区域。
   * The tab body, rendered by the host and filling everything below the tab row.
   */
  content: ReactNode;
}

/**
 * 目录页签：一个目录的若干级摘要，例如 L0 摘要与 L1 总览。
 *
 * 它和文件页签的区别在于**正文的层级**：文件页签只有正文；目录页签在标题行
 * 之下、正文之上还有一层同级视图切换（下划线页签），每一级各自是一份
 * Markdown。这一层属于目录这种实体，不属于所有文件页签，因此单独开一类，
 * 而不是给文件页签加一个通用插槽——那样每个文件页签都会多一个它用不到的
 * 空位。
 *
 * A tab for a directory's summary levels, for example its L0 abstract and L1
 * overview.
 *
 * It differs from a file tab in the layering of the body: a file tab has a body
 * and nothing else, while a directory tab has one more level between the header
 * and the body — an underlined switch between sibling views, each holding its own
 * Markdown. That level belongs to the directory entity rather than to every file
 * tab, so it gets its own kind instead of a generic slot on file tabs, which would
 * leave every file tab with an empty space it never uses.
 */
export interface FilePreviewDirectoryTab extends FilePreviewTabBase {
  /**
   * 页签类型标记，固定为 `directory`。
   * Tab kind discriminator, always `directory`.
   */
  kind: 'directory';
  /**
   * 摘要是否还在读取。与文件页签同义：为真时正文区显示加载态，外壳照常。
   * Whether the summary is still being read. Same as a file tab: the body shows
   * the loading state while the shell stays put.
   */
  loading?: boolean;
  /**
   * 可切换的摘要层级，至少一级。每级用 `source` 交给组件按格式渲染，与文件
   * 页签的正文同一条渲染路径。
   * The summary levels to switch between, at least one. Each level's `source` goes
   * through the same rendering path as a file tab's body.
   */
  levels: readonly [FilePreviewDirectoryLevel, ...FilePreviewDirectoryLevel[]];
  /**
   * 受控的当前层级 key。传入时由宿主决定层级，组件只上报切换意图。
   * The controlled active level key. When given, the host owns the level and the
   * component only reports switch intent.
   */
  activeLevelKey?: string;
  /**
   * 非受控时的初始层级 key，省略时选中第一级。
   * Initial level key when uncontrolled; the first level is selected when omitted.
   */
  defaultLevelKey?: string;
  /** 目录摘要的伴随图片资源。 Companion images shared by directory summary levels. */
  resources?: readonly FileViewerResource[];
  /** 宿主解析当前摘要的 Markdown 图片。 Host resolution of Markdown images in the active summary. */
  resolveImage?: FilePreviewFileTab['resolveImage'];
  /**
   * 当前层级变化后触发，参数为新的层级 key。
   * Called after the active level changes, with the new level key.
   */
  onActiveLevelChange?: (key: string) => void;
  /**
   * 目录操作的执行能力，例如沿路径向上打开。省略对应方法时该操作不可用。
   * Directory operation capabilities, for example opening an ancestor. Omitting a
   * method makes its operation unavailable.
   */
  operations?: FilePreviewOperations;
  /**
   * 目录页签的插槽。标题行动作与状态兜底与文件页签一致。
   * Slots of a directory tab. Header actions and state fallbacks match a file tab.
   */
  slots?: FilePreviewFileTabSlots;
}

/**
 * 目录的一级摘要。One summary level of a directory.
 */
export interface FilePreviewDirectoryLevel {
  /**
   * 层级唯一标识，用于切换与受控状态。
   * Unique level key, used for switching and controlled state.
   */
  key: string;
  /**
   * 页签行显示的名称，例如「L0 摘要」。
   * What the level switch shows, for example "L0 Summary".
   */
  label: ReactNode;
  /**
   * 该级的正文来源，与文件页签的 `source` 同形，交给组件按格式渲染。
   * The body source of this level, the same shape as a file tab's `source`, handed
   * to the component for format rendering.
   */
  source: FilePreviewSource;
  /** 该级独立的伴随资源。 Companion resources specific to this level. */
  resources?: readonly FileViewerResource[];
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
  /**
   * 打开正文里被点击的链接，完成后返回。传入后由宿主接管导航，组件阻止默认
   * 跳转；相对路径的解析也归宿主，组件给出的是 Markdown 里的原始目标。
   *
   * 目前只有 Markdown 正文会触发：其它格式的渲染器不提供链接点击回调。
   *
   * Opens a link clicked inside the body and resolves on completion. Supplying it
   * hands navigation to the host — the component prevents the default jump — and
   * resolving relative paths is the host's job too; the component passes the raw
   * target as written in the Markdown.
   *
   * Only Markdown bodies fire this today: the other renderers expose no link-click
   * callback.
   */
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
 * 交给宿主的动作上下文：组件持有的视图与编辑状态，以及改变它们的方法。
 *
 * 组件只渲染外壳与正文，不内置复制、下载、视图切换、编辑这些按钮；宿主在
 * `slots.actions` 里用这份上下文自己拼。状态仍由组件维护，宿主不必接管。
 *
 * The action context handed to the host: the view and editing state the component
 * owns, plus the methods that change it.
 *
 * The component renders only the shell and the body — it ships no copy, download,
 * view-switch or edit buttons. A host composes them in `slots.actions` from this
 * context. The state stays with the component; the host does not take it over.
 */
export interface FilePreviewActions {
  /** 当前视图模式。The current view mode. */
  viewMode: FilePreviewViewMode;
  /** 切换到指定视图模式。Switches to the given view mode. */
  setViewMode: (mode: FilePreviewViewMode) => void;
  /**
   * 该页签的格式是否同时支持预览与源码。为 `false` 时不该渲染切换控件。
   * Whether the format supports both preview and source. Do not render a switch
   * when it is `false`.
   */
  canSwitchView: boolean;
  /** 是否处于编辑态。Whether the tab is being edited. */
  editing: boolean;
  /** 是否有保存或读取在进行中。Whether a save or load is in flight. */
  busy: boolean;
  /** 该页签是否可编辑，即可提供 `editing`。Whether the tab is editable. */
  canEdit: boolean;
  /** 进入编辑态。Enters editing. */
  startEditing: () => void;
  /** 保存草稿，成功返回 `true`。Saves the draft, resolving to `true` on success. */
  saveEditing: () => Promise<boolean>;
  /** 放弃草稿并退出编辑态。Discards the draft and leaves editing. */
  cancelEditing: () => void;
  /** 该页签是否有可复制的正文。Whether the tab has copyable body text. */
  canCopyContent: boolean;
  /** 复制正文，成功返回 `true`。Copies the body, resolving to `true` on success. */
  copyContent: () => Promise<boolean>;
  /** 该页签是否有可复制的路径。Whether the tab has a copyable path. */
  canCopyPath: boolean;
  /** 复制路径，成功返回 `true`。Copies the path, resolving to `true` on success. */
  copyPath: () => Promise<boolean>;
  /** 该页签是否可下载。Whether the tab can be downloaded. */
  canDownload: boolean;
  /** 下载当前文件。Downloads the current file. */
  download: () => void;
}

/**
 * 文件页签内容区标题行的插槽。内容页签不消费这些——它整块正文由宿主渲染，
 * 没有组件提供的标题行可言。
 *
 * Slots of a file tab's content header. Content tabs do not use them: their whole
 * body is the host's, so there is no component-provided header to fill.
 */
export interface FilePreviewHeaderSlots {
  /**
   * 标题行右侧的动作区。组件不内置任何按钮，复制、下载、视图切换、编辑都由
   * 宿主用上下文里的状态与方法自行渲染。
   *
   * The action area on the right of the header. The component ships no buttons of
   * its own: copy, download, view switch and editing are all rendered by the host
   * from the state and methods in the context.
   */
  actions?: ReactNode | ((actions: FilePreviewActions) => ReactNode);
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
   * 格式不支持时的兜底内容。格式是否可预览由底座判定，因此这个节点会交给
   * 底座，而不是在组件这边提前判断。传了就由它接管整个不支持态（含底座原本
   * 会给出的下载入口）；不传时使用内置提示。
   *
   * Fallback when the format cannot be previewed. Whether a format can be
   * previewed is the base component's call, so this node is handed to it rather
   * than decided here. Supplying it takes over the whole unsupported state,
   * including the download entry the base would otherwise offer; omit it for the
   * built-in message.
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
 * 可替换文案，组件内置中英文文案。
 * Replaceable copy with built-in Chinese and English defaults.
 */
export interface FilePreviewLabels {
  /** 展开折叠路径层级的无障碍名称。Accessible name for hidden path levels. */
  showHiddenPath?: string;
  /**
   * 复制路径按钮的文案。这是组件自己渲染的唯一按钮——其余动作按钮由宿主在
   * `slots.actions` 里自绘，文案也归宿主。
   *
   * Label of the copy-path button. It is the only button the component renders
   * itself; every other action button belongs to the host's `slots.actions`,
   * copy included.
   */
  copyPath?: string;
  /** 编辑区文本框的无障碍名称。Accessible name of the editor textarea. */
  editor?: string;
  /** 读取失败后退出编辑态的按钮文案。Label of the button that leaves editing after a failed load. */
  leaveEditing?: string;
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
  /** 内置中英文文案的语言。Language of the built-in interface copy. */
  locale: Locale;
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
   * 当前页签变化后触发，参数为新的页签 key。
   * Called after the active tab changes, with the new tab key.
   */
  onActiveKeyChange?: (key: string) => void;
  /**
   * 所有页签的默认文案，跟随 locale。页签可以逐项覆盖。
   * Default copy for every tab, following locale. A tab can override it field
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
