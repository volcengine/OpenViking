import type {
  ForwardRefExoticComponent,
  HTMLAttributes,
  PropsWithoutRef,
  ReactNode,
  RefAttributes,
} from 'react';

/**
 * 所有行的公共字段。行是按宿主展平后的顺序渲染的，因此 `id` 与 `depth` 每行都要有。
 *
 * Fields shared by every row. Rows render in the host-flattened order, so each
 * one carries its own `id` and `depth`.
 */
export interface FileTreeRowBase {
  /**
   * 行唯一标识，用于选中与定位。
   * Unique row identity, used for selection and lookup.
   */
  id: string;
  /**
   * 缩进层级，根层为 `0`。
   * Indentation level; the root level is `0`.
   */
  depth: number;
}

/**
 * 子节点区域的行。这些行挂在某个父节点下方，用 `parentId` 表示归属。
 *
 * A row inside a children area. These rows sit under a parent node and use
 * `parentId` to name it.
 */
export interface FileTreeChildRow extends FileTreeRowBase {
  /**
   * 所属父节点的 id。整体加载失败、没有具体父节点时省略。
   * Id of the owning parent; omitted when the failure is not tied to a parent.
   */
  parentId?: string;
}

/**
 * 树中一条可见的行。宿主把自己的树状态展开成有序行列表后传入，
 * 因此展开、分页、加载、错误行都由调用方决定，组件不感知树结构。
 *
 * One visible row of the tree. The host flattens its own tree state into this
 * ordered list, so expansion, pagination, loading and error rows all come from
 * the caller and the component never inspects the tree structure.
 */
export type FileTreeRow =
  | FileTreeEntryRow
  | FileTreeLoadingRow
  | FileTreeLoadMoreRow
  | FileTreeErrorRow
  | FileTreeEmptyRow;

/**
 * 一个目录或文件行。只保留渲染必需的字段，宿主负责把自身数据映射到该形状。
 *
 * A directory or file row. Only what rendering needs is kept here; the host
 * maps its own data onto this shape.
 */
export interface FileTreeEntryRow extends FileTreeRowBase {
  /**
   * 行类型标记，固定为 `entry`。
   * Row kind discriminator, always `entry`.
   */
  kind: 'entry';
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
   * 节点类型：目录或文件。
   * Node type: directory or file.
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
   * 是否还有可加载的子节点。目录行未提供该字段时按「可以展开」处理，
   * 因此宿主在展开前无法得知子节点时无需传值；明确没有子节点时传 `false`。
   * Whether children can be loaded. A directory row without this field is
   * treated as expandable, so a host that cannot know before expanding can
   * omit it; pass `false` when the directory is known to be childless.
   */
  hasChildren?: boolean;
  /**
   * 当前是否处于展开状态。
   * Whether the node is currently expanded.
   */
  expanded?: boolean;
  /**
   * 行可见但不可打开或选中，例如没有详情权限的节点。
   * The row is visible but cannot be opened or selected, for example a node
   * without detail permission.
   */
  disabled?: boolean;
  /**
   * 名称下方的次要说明，例如摘要或行内描述。
   * Secondary line under the name, for example an abstract or inline
   * description.
   */
  description?: string;
  /**
   * 简短标记，例如摘要层级或上下文层级。不需要时省略。
   * Short badge, for example a summary level or context level; omit it when
   * unused.
   */
  levelLabel?: string;
  /**
   * 是否显示该节点上有进行中的写入。
   * Shows an in-flight write on this node.
   */
  busy?: boolean;
  /**
   * 过期时间，未知时为 `null`。宿主没有过期策略时省略。
   * Expiry time, or `null` when unknown; omit it when the host has no expiry
   * policy.
   */
  expiresAt?: string | null;
}

/**
 * 子节点加载中的行，插在父节点下方占位。
 * A row shown under a parent while its children are loading.
 *
 * 宿主在子节点区域渲染加载占位时产出这类行；也可以不产出，
 * 改用行内加载指示表达。
 * The host emits this row when the children area shows a loading placeholder;
 * it may also skip the row and use an inline indicator instead.
 */
export interface FileTreeLoadingRow extends FileTreeChildRow {
  /**
   * 行类型标记，固定为 `loading`。
   * Row kind discriminator, always `loading`.
   */
  kind: 'loading';
  /**
   * 正在加载子节点的父节点 id。
   * Id of the parent whose children are loading.
   */
  parentId: string;
}

/**
 * 子节点尚未加载时的分页行，提供继续加载的入口。
 * A pagination row for children the host has not loaded yet; the host uses it
 * to offer a load-more entry.
 *
 * 宿主按页取子节点时产出这类行；一次性返回全部子节点时不产出。
 * The host emits this row when children are paged; it is not emitted when all
 * children arrive in one response.
 */
export interface FileTreeLoadMoreRow extends FileTreeChildRow {
  /**
   * 行类型标记，固定为 `load-more`。
   * Row kind discriminator, always `load-more`.
   */
  kind: 'load-more';
  /**
   * 需要继续加载子节点的父节点 id。
   * Id of the parent whose children should be loaded next.
   */
  parentId: string;
  /**
   * 下一页是否正在加载。
   * Whether the next page is loading.
   */
  loading?: boolean;
}

/**
 * 子节点加载失败的行，提供重新加载的入口。
 * A failed children load with a retry entry.
 */
export interface FileTreeErrorRow extends FileTreeChildRow {
  /**
   * 行类型标记，固定为 `error`。
   * Row kind discriminator, always `error`.
   */
  kind: 'error';
  /**
   * 失败原因，未提供时显示默认文案。
   * Failure reason; the default copy is used when omitted.
   */
  message?: string;
}

/**
 * 已展开但当前没有子节点的目录。
 * An expanded directory that currently has no children.
 */
export interface FileTreeEmptyRow extends FileTreeChildRow {
  /**
   * 行类型标记，固定为 `empty`。
   * Row kind discriminator, always `empty`.
   */
  kind: 'empty';
  /**
   * 该空目录的 id。
   * Id of the empty directory.
   */
  parentId: string;
}

/**
 * 可替换文案，组件内置英文默认值。
 * Replaceable copy; the component ships English defaults.
 */
export interface FileTreeLabels {
  /**
   * 空树提示。
   * Empty tree message.
   */
  empty?: string;
  /**
   * 加载失败提示。
   * Load failure message.
   */
  error?: string;
  /**
   * 重试按钮文案。
   * Retry button label.
   */
  retry?: string;
  /**
   * 加载更多按钮文案。
   * Load-more button label.
   */
  loadMore?: string;
  /**
   * 子节点加载中的提示。
   * Message shown while children load.
   */
  loading?: string;
  /**
   * 展开按钮的无障碍名称，`{name}` 会被替换为节点名。
   * Accessible name of the expand control; `{name}` is replaced with the node
   * name.
   */
  expand?: string;
  /**
   * 折叠按钮的无障碍名称，`{name}` 会被替换为节点名。
   * Accessible name of the collapse control; `{name}` is replaced with the
   * node name.
   */
  collapse?: string;
}

/**
 * 树的插槽。目前只有工具栏，页面层可自行决定内容与交互。
 * Slots of the tree. Only the toolbar for now; the page layer owns its content
 * and interaction.
 */
export interface FileTreeSlots {
  /**
   * 树上方的工具栏，例如搜索、导入来源、刷新。
   * Toolbar above the tree, for example search, import sources or refresh.
   */
  toolbar?: ReactNode;
}

/**
 * 行的 DOM 属性约定。组件为每条行元素设置该属性，取值等于行的 `id`，
 * 页面层据此定位行，例如做一次性高亮、滚动定位或覆盖层。
 *
 * Row DOM attribute. The component sets it on every row element with the row
 * `id` as its value, so the page layer can locate a row for a transient
 * highlight, scroll targeting or an overlay.
 */
export const FILE_TREE_ROW_ATTRIBUTE = 'data-file-tree-row-id';

/**
 * 组件通过 ref 暴露的命令式能力，供页面层做一次性 UI 效果。
 * Imperative capabilities exposed through the ref, for one-off page-level
 * effects.
 */
export interface FileTreeHandle {
  /**
   * 取得某行的根元素，未渲染时返回 `null`。
   * Returns the row root element, or `null` when the row is not rendered.
   */
  getRowElement: (id: string) => HTMLElement | null;
  /**
   * 把某行滚动到可见区域，返回是否找到该行。
   * Scrolls a row into view and reports whether it was found.
   */
  scrollToRow: (id: string, options?: ScrollIntoViewOptions) => boolean;
}

/** 文件树操作的执行能力。File-tree operation capabilities. */
export interface FileTreeOperations {
  /** 选中条目。Selects an entry. */
  select?: (row: FileTreeEntryRow) => Promise<void>;
  /** 展开或折叠目录。Expands or collapses a directory. */
  toggleExpand?: (row: FileTreeEntryRow) => Promise<void>;
  /** 加载下一页子节点。Loads the next page of children. */
  loadMore?: (row: FileTreeLoadMoreRow) => Promise<void>;
  /** 重新加载失败的子节点。Retries a failed children load. */
  retry?: (row: FileTreeErrorRow) => Promise<void>;
  /** 打开保留策略设置。Opens retention policy settings. */
  openRetentionSettings?: (uri: string) => Promise<void>;
}

/**
 * 目录树的公开接口。
 * Public interface of the file tree.
 *
 * 组件渲染行并通知成功的操作；工具栏、搜索、导入、权限流程都由页面层实现，
 * 通过 `toolbar` 插槽注入。
 * The component renders rows and reports successful operations. Toolbars, search,
 * import and permission flows belong to the page layer and are injected
 * through the `toolbar` slot.
 */
export interface FileTreeProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'children' | 'onSelect'
> {
  /**
   * 按渲染顺序排列的可见行。
   * Visible rows in render order.
   */
  rows: readonly FileTreeRow[];
  /**
   * 操作的执行能力；省略对应方法时禁用该操作。
   * Operation capabilities; omitting a method disables its operation.
   */
  operations?: FileTreeOperations;
  /**
   * 当前选中的行 id，由宿主持有。
   * Selected row id, owned by the host.
   */
  selectedId?: string | null;
  /**
   * 首次加载是否已完成。
   * Whether the tree has finished its first load.
   */
  isReady?: boolean;
  /**
   * 首次加载期间替代行列表显示的内容。
   * Rendered instead of rows while the first load runs.
   */
  loadingFallback?: ReactNode;
  /**
   * 整体加载失败时替代行列表显示的内容，通常包含标题与重试入口。
   * Rendered instead of rows when the whole tree failed to load, usually with a
   * title and a retry entry.
   */
  errorFallback?: ReactNode;
  /**
   * 树中没有条目时显示的内容。
   * Rendered instead of rows when the tree has no entries.
   */
  emptyFallback?: ReactNode;
  /**
   * 树的插槽。工具栏由页面层实现，可以是搜索、导入来源、刷新等任意组合。
   * Slots of the tree. The toolbar is implemented by the page layer and can
   * hold search, import sources, refresh or any other combination.
   */
  slots?: FileTreeSlots;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: FileTreeLabels;
  /**
   * 为行元素追加属性。返回的 `className` 会与组件自身的类名合并，
   * 其余属性覆盖到行元素上，用于一次性高亮、埋点或自定义 `data-*`。
   * Extra attributes for a row element. The returned `className` is merged with
   * the component's own classes and the remaining attributes are applied to
   * the row element, for one-off highlights, analytics or custom `data-*`.
   */
  getRowAttributes?: (row: FileTreeRow) => HTMLAttributes<HTMLDivElement>;
  /**
   * 渲染行尾的看板指示器。返回 `null` 时该行不显示指示器。内容与交互都由
   * 宿主决定，包括点击行为与折叠时的数量角标。
   * Renders the dashboard indicator at the end of a row. Returning `null` shows
   * no indicator. Both the content and its interaction belong to the host,
   * including the click behaviour and any collapsed-count badge.
   */
  renderDashboard?: (row: FileTreeEntryRow) => ReactNode;
  /**
   * 渲染行的悬停浮层内容。返回 `null` 时不展示；延迟、定位与悬停抑制
   * 由组件处理。
   * Renders the hover content of a row. Returning `null` shows nothing; the
   * component handles the delay, positioning and hover suppression.
   */
  renderRowTooltip?: (row: FileTreeEntryRow) => ReactNode;
  /**
   * 条目成功选中后触发，参数为选中的条目；失败时不触发。
   * Called after an entry is selected successfully; not called on failure.
   */
  onSelect?: (row: FileTreeEntryRow) => void;
  /**
   * 目录成功展开或折叠后触发，参数为操作的条目；失败时不触发。
   * Called after a directory expands or collapses successfully; not called on failure.
   */
  onToggleExpand?: (row: FileTreeEntryRow) => void;
  /**
   * 下一页子节点加载成功后触发；失败时不触发。
   * Called after the next page of children loads successfully; not called on failure.
   */
  onLoadMore?: (row: FileTreeLoadMoreRow) => void;
  /**
   * 重新加载子节点成功后触发；重试失败时不触发。
   * Called after retrying a children load succeeds; not called on failure.
   */
  onRetry?: (row: FileTreeErrorRow) => void;
  /**
   * 保留策略设置成功打开后触发，参数为对应 URI；失败时不触发。
   * Called after retention settings open successfully, with the URI; not called on failure.
   */
  onOpenRetentionSettings?: (uri: string) => void;
}

/**
 * 目录树组件，可通过 ref 查找行或滚动到指定行。
 * A file-tree component whose ref supports row lookup and scrolling.
 */
export type FileTreeComponent = ForwardRefExoticComponent<
  PropsWithoutRef<FileTreeProps> & RefAttributes<FileTreeHandle>
>;
