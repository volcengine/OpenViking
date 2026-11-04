import type { FileViewerResource } from '@ark-design/ui/file-viewer/core';
import type {
  ForwardRefExoticComponent,
  HTMLAttributes,
  PropsWithoutRef,
  ReactNode,
  RefAttributes,
} from 'react';

/**
 * 要预览的内容。宿主只给出内容本身与文件名，格式识别与渲染器加载都在内部完成。
 *
 * 组件会把这里的三种来源归一化成 FileViewer 的 `FileSource`：`content` 会被
 * 包成以 `fileName` 命名的 `File`，`file` 与 `url` 直接透传。三者按
 * `content`、`file`、`url` 的顺序取第一个有值的。
 *
 * The content to preview. The host only supplies the content and the file
 * name; format detection and renderer loading happen inside the component.
 *
 * The component normalizes these three sources into FileViewer's `FileSource`:
 * `content` is wrapped into a `File` named after `fileName`, while `file` and
 * `url` pass through. The first one that is set wins in the order `content`,
 * `file`, `url`.
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
 * 所有页签的公共字段：页签行的标识与展示，以及内容区标题行里与内容无关的
 * 部分（路径面包屑、复制路径、标题行插槽）。
 *
 * 预览/源码切换只属于文件页签：它由组件按文件格式决定是否可用，内容页签的
 * 正文由宿主自己渲染，需要两种视图时由宿主在 `content` 里自行切换。
 *
 * Fields shared by every tab: the tab row's identity and display, plus the parts
 * of the content header row that do not depend on the content — path breadcrumb,
 * copy path and the header slots.
 *
 * The preview/source switch belongs to file tabs only: the component decides
 * whether a file format has both views, while a content tab's body is rendered
 * by the host, which switches between its own views inside `content`.
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
   * 跳转到上级目录。内容区标题行的路径面包屑据此渲染返回入口。
   *
   * 与 `onCopyPath` 一样作用于路径行，因此两类页签都可以传。
   *
   * Navigates to the parent directory. The path breadcrumb in the content
   * header row renders a back entry from it.
   *
   * Like `onCopyPath` it acts on the path row, so both tab kinds can pass it.
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
 * 一个页签。页签的集合由宿主给出，当前激活哪个由组件内部维护，宿主不感知。
 *
 * 两类页签的渲染路径完全不同：
 *
 * - `kind: 'file'` 走 FileViewer：组件按 `source` 的扩展名与 `fileType` 选
 *   渲染器，带视图切换、编辑与各类状态兜底。
 * - `kind: 'content'` 不走 FileViewer：组件直接把 `content` 渲染出来，
 *   用于看板、图谱这类宿主完全自定义的内容。
 *
 * A tab. The host supplies the list and the component tracks which one is
 * active; the host does not.
 *
 * The two kinds render through completely different paths:
 *
 * - `kind: 'file'` goes through FileViewer: the component picks a renderer from
 *   `source`'s extension and `fileType`, and offers view switching, editing and
 *   the state fallbacks.
 * - `kind: 'content'` bypasses FileViewer: the component renders `content`
 *   directly, for dashboards and graphs the host fully owns.
 */
export type FilePreviewTab = FilePreviewFileTab | FilePreviewContentTab;

/**
 * 文件页签，经 FileViewer 按格式渲染 `source`。
 * A file tab rendered by FileViewer according to `source`'s format.
 */
export interface FilePreviewFileTab extends FilePreviewTabBase {
  /**
   * 页签类型标记，固定为 `file`。
   * Tab kind discriminator, always `file`.
   */
  kind: 'file';
  /**
   * 要渲染的内容与文件名。组件按扩展名与 `fileType` 选择渲染器，内部会归一化
   * 成 FileViewer 的来源类型。
   * The content and file name to render. The component picks a renderer from the
   * extension and `fileType`, normalizing the value into FileViewer's source
   * type internally.
   */
  source: FilePreviewSource;
  /**
   * 主文件之外、按 key 读取的伴随资源，例如 3D 模型的贴图。
   * Companion resources read by key alongside the main file, for example
   * textures of a 3D model.
   */
  resources?: readonly FileViewerResource[];
  /**
   * 编辑能力；不传即为只读。编辑态与草稿由组件内部维护。
   * Editing capability; omit for a read-only preview. The editing state and
   * draft are owned by the component.
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
 * 内容页签，由宿主完全自定义渲染，不经过 FileViewer。
 *
 * 它同样有内容区标题行（路径面包屑、权限标记、复制路径与标题行插槽），但没有
 * `source`、`resources`、`editing`、预览/源码切换与三类状态兜底——这些都属于
 * 格式渲染的范畴，加载与失败由宿主在 `content` 里自行处理。
 *
 * A content tab fully rendered by the host, bypassing FileViewer.
 *
 * It still has the content header row (path breadcrumb, permission tag, copy
 * path and the header slots), but no `source`, `resources`, `editing`, the
 * preview/source switch or state fallbacks — those belong to format rendering,
 * and loading and failure are handled by the host inside `content`.
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
 * 编辑能力。不传即为只读。
 *
 * 编辑态、草稿与保存中状态都由组件内部维护，宿主不需要也不应该感知；
 * 宿主只负责两件事：进入编辑前提供内容，以及收到保存请求时落盘。
 *
 * Editing capability. Omit it for a read-only preview.
 *
 * The editing state, the draft and the saving state all live inside the
 * component; the host neither sees nor controls them. The host only provides
 * the content before editing starts and persists it when a save is requested.
 */
export interface FilePreviewEditing {
  /**
   * 用户点击编辑时调用，返回进入编辑所需的完整内容。
   *
   * 不传时组件直接用页签里已有的内容进入编辑：`content` 直接使用，
   * `file` 读取其文本，`url` 因无法在浏览器端直接读取而视为不可编辑。
   *
   * 传入后组件改用返回值，适用于预览内容被截断、需要另行拉取全文的场景；
   * 返回 `undefined` 表示放弃本次编辑，组件保持预览态。
   *
   * Invoked when the user starts editing, returning the full content to edit.
   *
   * When omitted the component edits what the tab already holds: `content`
   * is used as is, `file` is read as text, and `url` counts as not editable
   * because the browser cannot read it directly.
   *
   * When provided the return value wins, which suits truncated previews that
   * need the full text fetched elsewhere. Returning `undefined` abandons the
   * edit and the component stays in the preview state.
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
   *
   * 用户点取消、切换到其他页签、关闭预览都会触发。草稿只在组件内部维护，
   * 宿主通过这里才能拿到未保存的内容，用于提示或记录。
   *
   * 保存成功不属于取消，不会触发该回调。
   *
   * Invoked when editing ends and the draft is discarded; the argument is the
   * discarded draft.
   *
   * Triggered by the cancel control, by switching to another tab and by
   * closing the preview. The draft lives inside the component, so this is the
   * only way the host can see unsaved content, for a warning or a log entry.
   *
   * A successful save is not a cancellation and does not trigger it.
   */
  onCancel?: (value: string) => void;
}

/**
 * 内容区标题行的插槽，所有页签共用。
 *
 * 注意这里与 FileViewer 自带的 `FileViewerSlots` 不是同一组：那一组是
 * `confirm` / `downloadFallback` / `error` / `loading` 的状态覆盖，由本组件
 * 内部转接。
 *
 * Slots of the content header row, shared by every tab.
 *
 * These are not the same as FileViewer's own `FileViewerSlots`, which covers the
 * `confirm`, `downloadFallback`, `error` and `loading` states and is forwarded
 * internally by this component.
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
 * 文件页签的标题行插槽。在公共插槽之上增加三类状态兜底，因为只有文件页签
 * 经 FileViewer 渲染，才存在加载、失败与格式不支持这三种状态。
 *
 * Header slots of a file tab. Adds the three state fallbacks on top of the
 * shared ones, because only a file tab renders through FileViewer and therefore
 * has loading, failure and unsupported states.
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
   * 二进制文件尚未接入渲染器时的提示。
   * Message shown while binary rendering is not wired up yet.
   */
  binaryUnsupported?: string;
}

/**
 * 组件通过 ref 暴露的命令式能力，用于从外部切换页签。
 *
 * 页签的激活项由组件内部维护，因此外部要跳到某个页签时通过这里触发，而不是
 * 受控 props。典型场景是点击文件树里的看板指示器后切到对应的看板页签。
 *
 * Imperative capabilities exposed through the ref, used to switch tabs from
 * outside.
 *
 * The active tab lives inside the component, so switching to a tab from outside
 * goes through here rather than a controlled prop. A typical case is clicking a
 * dashboard indicator in the file tree and landing on that dashboard tab.
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
 * 文件预览的公开接口。
 * Public interface of the file viewer.
 *
 * 内容由页签驱动：每个页签用 `source` 交给组件按格式渲染，或用 `content`
 * 完全自定义渲染。当前激活哪个页签由组件内部维护，宿主不感知。
 *
 * 页签行与内容区标题行都由组件内置：页签行显示标题与图标；标题行显示路径
 * 面包屑、权限标记、复制与下载按钮，以及文件页签的预览/源码切换与编辑入口。
 * 除页签列表本身与默认文案外，其余能力都挂在页签上。
 *
 * 格式识别、渲染器按需加载、加载与失败的分支、文件页签的视图切换与编辑态都在
 * 内部处理，以 FileViewer 作为渲染底座，内置支持的格式集合固定，不对外开放。
 *
 * Content is driven by tabs: each tab either hands `source` to the component
 * for format rendering or renders fully custom `content`. Which tab is active
 * is handled inside the component.
 *
 * The tab row and the content header row are both built in: the tab row shows
 * a title and an icon, while the header row shows the path breadcrumb,
 * permission tag, copy and download buttons, plus the preview/source switch and
 * the edit entry for file tabs. Apart from the tab list and the default copy,
 * everything hangs off a tab.
 *
 * Format detection, lazy renderer loading, the loading and failure branches,
 * file-tab view switching and the editing state are all handled inside, with
 * FileViewer as the rendering base. The supported format set is built in and
 * not exposed for override.
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
 * 文件预览组件类型。组件以 `forwardRef` 实现，`ref` 通过第二个参数接收，
 * 因此 React 18 与 19 都能拿到 `FilePreviewHandle`；React 19 里写成普通
 * `ref` prop 也照常工作。
 *
 * Component type. The implementation uses `forwardRef`, so the ref arrives as
 * the second parameter and yields a `FilePreviewHandle` on both React 18 and
 * 19; on React 19 passing it as a plain `ref` prop works as well.
 */
export type FilePreviewComponent = ForwardRefExoticComponent<
  PropsWithoutRef<FilePreviewProps> & RefAttributes<FilePreviewHandle>
>;
