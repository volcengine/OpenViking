import type { Locale } from '../../shared/locale.js';
import type {
  ForwardRefExoticComponent,
  HTMLAttributes,
  PropsWithoutRef,
  ReactNode,
  RefAttributes,
} from 'react';

/**
 * 所有行的公共字段。宿主负责展平行，因此每行都需要 `id` 与 `depth`。
 *
 * Fields shared by every host-flattened row. Each row carries its own `id` and `depth`.
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
 * 展开、分页、加载、错误行都由调用方决定。
 *
 * One visible row of the tree. The host flattens its own tree state into this
 * ordered list and supplies expansion, pagination, loading and error rows.
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
  /** 允许展开但不选中，例如仅能遍历的目录。Allows expansion without selection. */
  selectable?: boolean;
  /** 权限状态的纯展示标签，不执行鉴权。Display-only permission status. */
  access?: 'read-only' | 'denied' | 'container';
  /**
   * 名称右侧的次要说明，例如摘要或行内描述。
   * Secondary text beside the name on the same row, for example an abstract or
   * inline description.
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
 * 目录语义键。组件按行 `uri` 的路径结构判定，用它取说明与触发行为文案。
 *
 * 判定规则属于组件内部实现，宿主只需按需覆盖文案；键为空表示该路径没有内置说明。
 *
 * Semantic key of a directory. The component derives it from the structure of a
 * row's `uri` and uses it to pick the description and trigger copy.
 *
 * The matching rules are internal; a host only overrides copy. An unmatched path
 * has no built-in description.
 */
export type FileTreeInfoKey =
  /**
   * 用户根目录，`viking://user/{id}`。
   * User root, `viking://user/{id}`.
   */
  | 'userRoot'
  /**
   * 长期记忆，`/memories`。
   * Long-term memory, `/memories`.
   */
  | 'memories'
  /**
   * 按应用或工作区隔离的上下文，`/peers`。
   * Context isolated per application or workspace, `/peers`.
   */
  | 'peers'
  /**
   * 密钥等敏感配置，`/privacy`。
   * Keys and other sensitive configuration, `/privacy`.
   */
  | 'privacy'
  /**
   * 用户私有资料，`/resources`。
   * Private user knowledge, `/resources`.
   */
  | 'userResources'
  /**
   * 会话与任务记录，`/sessions`。
   * Conversation and task records, `/sessions`.
   */
  | 'sessions'
  /**
   * 个人技能，`/skills`。
   * Personal skills, `/skills`.
   */
  | 'skills'
  /**
   * 用户身份与背景，`/memories/profile.md`。
   * User identity and background, `/memories/profile.md`.
   */
  | 'profile'
  /**
   * 个性化偏好，`/memories/preferences`。
   * Personalized preferences, `/memories/preferences`.
   */
  | 'preferences'
  /**
   * 人物、项目、组织等实体，`/memories/entities`。
   * People, projects and organizations, `/memories/entities`.
   */
  | 'entities'
  /**
   * 按日期归档的关键事件，`/memories/events`。
   * Key events archived by date, `/memories/events`.
   */
  | 'events'
  /**
   * Agent 名称与人设，`/memories/identity.md`。
   * Agent name and persona, `/memories/identity.md`.
   */
  | 'identity'
  /**
   * Agent 行为准则，`/memories/soul.md`。
   * Agent behaviour guidelines, `/memories/soul.md`.
   */
  | 'soul'
  /**
   * 用于经验训练的交互案例，`/memories/cases`。
   * Interaction cases used to train experience, `/memories/cases`.
   */
  | 'cases'
  /**
   * 可复用的任务执行轨迹，`/memories/trajectories`。
   * Reusable task trajectories, `/memories/trajectories`.
   */
  | 'trajectories'
  /**
   * 自动提炼的执行经验，`/memories/experiences`。
   * Distilled execution experience, `/memories/experiences`.
   */
  | 'experiences'
  /**
   * 数据库内共享的资料，`viking://resources`。
   * Knowledge shared across the database, `viking://resources`.
   */
  | 'sharedResources'
  /**
   * Agent 能力根目录，`viking://agent`。
   * Agent capability root, `viking://agent`.
   */
  | 'agentRoot'
  /**
   * Agent 的通信端点配置，`viking://agent/endpoints`。
   * Agent communication endpoint configuration, `viking://agent/endpoints`.
   */
  | 'agentEndpoints'
  /**
   * Agent 的支付能力配置，`viking://agent/payments`。
   * Agent payment capability configuration, `viking://agent/payments`.
   */
  | 'agentPayments'
  /**
   * Agent 的技能注册表，`viking://agent/skills`。
   * Agent skill registry, `viking://agent/skills`.
   */
  | 'agentSkills'
  /**
   * Agent 的工具配置，`viking://agent/tools`。
   * Agent tool configuration, `viking://agent/tools`.
   */
  | 'agentTools'
  /**
   * 记忆根目录，`viking://memory`。
   * Memory root, `viking://memory`.
   */
  | 'memoryRoot'
  /**
   * 外部知识根目录，`viking://resource`。
   * External knowledge root, `viking://resource`.
   */
  | 'resourceRoot'
  /**
   * 会话根目录，`viking://session`。
   * Session root, `viking://session`.
   */
  | 'sessionRoot'
  /**
   * 内置资产根目录，`viking://system`。
   * Built-in asset root, `viking://system`.
   */
  | 'systemRoot';

/**
 * 目录说明文案。未提供的键跟随 `locale`；传空字符串表示该键不显示。
 *
 * Directory copy. Keys left out follow `locale`; an empty string hides that
 * key.
 */
export interface FileTreeInfoLabels {
  /**
   * 说明，显示在浮层的「说明」一行，部分键也显示在行内。
   * Description, shown in the tooltip and, for some keys, inline.
   */
  description?: Partial<Record<FileTreeInfoKey, string>>;
  /**
   * 触发行为，说明该节点在什么条件下被写入或生成，只显示在浮层。
   * Trigger copy: when this node is written or generated. Tooltip only.
   */
  triggerBehavior?: Partial<Record<FileTreeInfoKey, string>>;
  /**
   * 浮层里各行的字段名。
   * Field names used inside the tooltip.
   */
  field?: {
    /**
     * 「说明」字段名。
     * Label of the description row.
     */
    description?: string;
    /**
     * 「触发行为」字段名。
     * Label of the trigger row.
     */
    triggerBehavior?: string;
    /**
     * 「路径」字段名。
     * Label of the path row.
     */
    path?: string;
  };
}

/**
 * 可替换文案，组件内置中英文文案。
 * Replaceable copy with built-in Chinese and English defaults.
 */
export interface FileTreeLabels {
  readOnly?: string;
  noAccess?: string;
  traverseOnly?: string;
  /** 搜索入口文案。Search trigger label. */
  search?: string;
  /** 刷新按钮的无障碍名称。Accessible refresh button label. */
  refresh?: string;
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
  /**
   * 目录说明与浮层字段名。省略时使用内置文案。
   * Directory copy and tooltip field names; built-in copy is used when omitted.
   */
  info?: FileTreeInfoLabels;
}

/**
 * 树的插槽。宿主可追加工具栏内容或替换加载指示器。
 * Tree slots for additional toolbar content and a custom loading indicator.
 */
export interface FileTreeSlots {
  /**
   * 树上方的工具栏，例如搜索、导入来源、刷新。
   * Toolbar above the tree, for example search, import sources or refresh.
   */
  toolbar?: ReactNode;
  /**
   * 替换目录加载子节点时展开箭头位置显示的转圈。省略时用内置的 CSS 圆环，
   * 传入自己的节点可以换成图标或品牌化的加载态；其中的 SVG 按 12 × 12 显示。
   * Replaces the spinner shown in the expand chevron slot while a directory
   * loads its children. Omitted uses the built-in CSS ring; pass a node to swap
   * in an icon or a branded loader. An SVG inside it renders at 12 × 12.
   */
  spinner?: ReactNode;
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
  /** 打开宿主搜索界面。Opens the host's search interface. */
  search?: () => Promise<void>;
  /** 刷新目录；等待期间自动显示加载态。Refreshes the tree with a pending state. */
  refresh?: () => Promise<void>;
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
   * 默认按传入顺序渲染。设为 `context` 时，只按 Viking URI 首段调整根目录及其子树。
   * Defaults to input order. `context` reorders root directory branches by Viking URI namespace.
   */
  rootOrder?: 'input' | 'context';
  /** 可选文案语言；继承上层，最终默认英文。Optional locale; inherits or defaults to en-US. */
  locale?: Locale;
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
   * 行尾插槽的位置，默认 `viewport-end` 吸附树的可视区域右侧；
   * `row-end` 位于完整行末尾并随内容横向滚动。
   * Positions the trailing slot. Defaults to `viewport-end`, pinned to the
   * tree's visible right edge; `row-end` scrolls with the full row content.
   */
  rowTrailingPosition?: 'row-end' | 'viewport-end';
  /**
   * 显示内置搜索入口；默认显示，未提供 operations.search 时禁用。
   * Shows the built-in search trigger. Visible by default; disabled without operations.search.
   */
  search?: boolean | { disabled?: boolean };
  /**
   * 显示内置刷新按钮；默认显示。loading 可表达宿主额外的刷新状态。
   * Shows the refresh button. Visible by default; loading adds a host-controlled pending state.
   */
  refresh?: boolean | { disabled?: boolean; loading?: boolean };
  /** 刷新按钮的受控加载态，与异步刷新、refresh.loading 合并。Host-controlled refresh pending state. */
  loading?: boolean;
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
   * 为行元素追加属性。返回的 `className` 会与组件自身的类名合并，
   * 其余属性覆盖到行元素上，用于一次性高亮、埋点或自定义 `data-*`。
   * Extra attributes for a row element. The returned `className` is merged with
   * the component's own classes and the remaining attributes are applied to
   * the row element, for one-off highlights, analytics or custom `data-*`.
   */
  getRowAttributes?: (row: FileTreeRow) => HTMLAttributes<HTMLDivElement>;
  /**
   * 渲染行尾标记，例如看板入口、状态角标或数量。返回 `null` 时该行不显示标记。
   * 内容与交互都由宿主决定，包括点击行为，位置由 `rowTrailingPosition` 控制。
   *
   * Renders a trailing marker at the end of a row, such as a board entry, a status
   * badge or a count. Returning `null` shows no marker. Both the content and its
   * interaction belong to the host, including the click behaviour.
   * `rowTrailingPosition` controls its position.
   */
  renderRowTrailing?: (row: FileTreeEntryRow) => ReactNode;
  /**
   * 渲染文件行的行首图标，例如按扩展名区分的类型图标。返回 `null` 或省略时用
   * 内置的通用文件图标；目录行始终用内置的文件夹图标。
   *
   * Renders the leading icon of a file row, for example a type icon chosen by
   * extension. Returning `null` or omitting it uses the built-in generic file
   * icon; directory rows always use the built-in folder icon.
   */
  renderRowIcon?: (row: FileTreeEntryRow) => ReactNode;
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
  /** 搜索界面成功打开后触发。Called after search opens successfully. */
  onSearch?: () => void;
  /** 刷新成功后触发，失败时不触发。Called after a successful refresh only. */
  onRefresh?: () => void;
  /**
   * 自定义工具栏位于内置搜索与刷新上方，可放导入等宿主操作。
   * A custom toolbar above built-in controls, for host actions such as import.
   */
  slots?: FileTreeSlots;
  /**
   * 可替换文案，未提供的项使用当前 locale 的内置文案。
   * Replaceable copy; omitted fields follow locale.
   */
  labels?: FileTreeLabels;
}

/**
 * 目录树组件，可通过 ref 查找行或滚动到指定行。
 * A file-tree component whose ref supports row lookup and scrolling.
 */
export type FileTreeComponent = ForwardRefExoticComponent<
  PropsWithoutRef<FileTreeProps> & RefAttributes<FileTreeHandle>
>;
