import type { HTMLAttributes, ReactNode } from 'react';

/**
 * 搜索框的显示阈值。已加载条数或 `total` 超过它，或关键字非空时，面板才渲染搜索框。
 * 组件内部按该值判断，不开放为 Props。
 *
 * Threshold above which a panel renders its search box. The panel shows search
 * once the loaded count or `total` exceeds it, or when a keyword is set. The
 * component applies it internally and does not expose it as a prop.
 */
export const SCOPE_SWITCHER_SEARCH_THRESHOLD = 10;

/**
 * 一个层级中的可选项。宿主把库、数据空间、账号或用户记录映射成该结构。
 *
 * One selectable value in a level. Hosts map their own library, data space,
 * account or user records onto this shape.
 */
export interface ScopeSwitcherOption {
  /**
   * 选项唯一标识。
   * Unique option key.
   */
  id: string;
  /**
   * 选项显示文本。搜索命中时由组件高亮匹配片段。
   * Option label. The component highlights the matching part while searching.
   */
  label: string;
  /**
   * 行首图标，例如库、数据空间或用户的头像。
   * Row leading icon, for example a library, data space or user avatar.
   */
  icon?: ReactNode;
  /**
   * 显示在标签下方的次要说明，例如套餐或地址。
   * Secondary line under the label, for example a plan or an address.
   */
  description?: string;
  /**
   * 是否禁用该选项：渲染置灰样式且不响应点击。
   * Whether the option is disabled: rendered dimmed and ignoring clicks.
   */
  disabled?: boolean;
}

/**
 * 面包屑中一个层级的公共字段：标识、触发器的无障碍名与展示内容。
 *
 * 层级分成两种形态，见 `ScopeSwitcherFixedLevel` 与
 * `ScopeSwitcherSelectableLevel`。同一行里两种形态可以混用，因此一个宿主可以
 * 只让部分层级可切换。
 *
 * Fields shared by every level: its identity, the trigger's accessible name and
 * what the trigger shows.
 *
 * A level is either fixed or selectable, see `ScopeSwitcherFixedLevel` and
 * `ScopeSwitcherSelectableLevel`. Both can be mixed in one row, so a host can
 * make only some levels switchable.
 */
export interface ScopeSwitcherLevelBase {
  /**
   * 稳定标识，例如 `library`、`account` 或 `user`。
   * Stable key, for example `library`, `account` or `user`.
   */
  key: string;
  /**
   * 触发器的无障碍名称，例如「切换库」。不传时不写 `aria-label`。
   * Accessible name of the trigger, for example "Switch library". No
   * `aria-label` is written when omitted.
   */
  ariaLabel?: string;
  /**
   * 触发器上展示的当前选中项文本。
   *
   * 由宿主单独给出，不要求等于 `options` 中对应项的 `label`：当前值可能不在已
   * 加载的那一页里，此时展示宿主给的文本。
   *
   * Text shown on the trigger for the current selection.
   *
   * Supplied by the host and not required to equal the matching `label` in
   * `options`: the current value may not be on the loaded page.
   */
  selectedLabel: string;
  /**
   * 触发器图标，也可以是头像。
   * Trigger icon, which can also be an avatar.
   */
  icon?: ReactNode;
  /**
   * 触发器末尾的标记，例如套餐名。不传时不渲染。
   * Tag at the end of the trigger, for example a plan name. Not rendered when
   * omitted.
   */
  tag?: ReactNode;
}

/**
 * 固定层级：只展示当前值，不渲染下拉，也不接收任何选项或面板字段。
 *
 * 用于当前作用域不可切换的情形，例如个人终端里跟随登录态固定的库与数据空间、
 * 管理模式下不能切换的数据空间。这类层级在视觉上同样是一条面包屑，只是没有
 * 展开箭头、不可点击，也不需要宿主伪造一份空选项列表。
 *
 * A fixed level: shows the current value only, renders no dropdown and takes no
 * option or panel fields.
 *
 * Used where the current scope cannot be switched, for example a library and
 * data space fixed by the sign-in in a personal terminal, or a data space that
 * cannot be switched in management mode. It still looks like a breadcrumb
 * segment, just without an expand affordance and not clickable, and the host
 * does not have to fabricate an empty option list.
 */
export interface ScopeSwitcherFixedLevel extends ScopeSwitcherLevelBase {
  /**
   * 判别字段，固定层级为 `'fixed'`。
   * Discriminant, always `'fixed'` for a fixed level.
   */
  kind: 'fixed';
}

/**
 * 可切换层级：渲染可展开的触发器与下拉面板。
 *
 * 面板结构、搜索、分页与选中状态全部受控，由宿主传入。同一行里可以只有一个
 * 可切换层级，也可以三个都可切换。
 *
 * A selectable level: renders an expandable trigger and its dropdown panel.
 *
 * The panel structure, search, paging and selection are all controlled and come
 * from the host. A row can hold one selectable level or several.
 */
export interface ScopeSwitcherSelectableLevel extends ScopeSwitcherLevelBase {
  /**
   * 判别字段，可切换层级为 `'selectable'`。
   * Discriminant, always `'selectable'` for a selectable level.
   */
  kind: 'selectable';
  /**
   * 可选项列表，按宿主给定的顺序渲染。
   * Selectable options, rendered in the order the host gives.
   */
  options: readonly ScopeSwitcherOption[];
  /**
   * 当前选中项 id，由宿主持有。命中项渲染勾选标记并使用
   * `role="option"` + `aria-selected` 表达状态。
   *
   * 必填。可切换层级始终有一个当前作用域，没有「尚未选中」的形态：宿主若还没取到
   * 数据，传空字符串，由 `selectedLabel` 决定触发器上显示什么。
   *
   * Selected option id, owned by the host. The matching option renders a check
   * mark and expresses its state with `role="option"` and `aria-selected`.
   *
   * Required. A selectable level always has a current scope and has no
   * "nothing selected" form: a host that has not loaded data yet passes an empty
   * string, and `selectedLabel` decides what the trigger shows.
   */
  selectedId: string;
  /**
   * 该层级的总条数，用于判断搜索框是否可见。不传时以已加载条数为准。
   * Total number of entries, used to decide whether the search box is shown.
   * The loaded count is used when omitted.
   */
  total?: number;
  /**
   * 选项列表是否正在首次加载。为 `true` 时面板显示加载态，不渲染列表。
   * Whether the option list is loading for the first time. While true the panel
   * shows a loading state instead of the list.
   */
  loading?: boolean;
  /**
   * 当前搜索关键字。非空时搜索框保持可见，并用于高亮命中片段。
   * Current search keyword. A non-empty value keeps the search box visible and
   * drives hit highlighting.
   */
  searchKeyword?: string;
  /**
   * 该层级是否暂时不可切换，例如权限尚未就绪。为 `true` 时触发器置灰、不响应
   * 点击，也不渲染下拉。恒为可切换的宿主不传。
   *
   * Whether the level is temporarily not switchable, for example while
   * permissions load. While true the trigger is dimmed, ignores clicks and
   * renders no dropdown. Hosts whose levels are always switchable leave it out.
   */
  disabled?: boolean;
  /**
   * 选项列表是否加载失败。为 `true` 时面板渲染 `errorFallback`。
   * Whether the option list failed to load. While true the panel renders
   * `errorFallback`.
   */
  error?: boolean;
  /**
   * 该层级是否有一个变更正在进行，例如正在删除某个库或正在切换数据空间。
   * 为 `true` 时组件禁用整个列表，避免并发切换。
   *
   * Whether a change is in flight for this level, for example deleting a library
   * or switching the data space. While true the component disables the whole
   * list so no second switch can start.
   */
  busy?: boolean;
  /**
   * 面板内除列表以外的全部内容，由宿主自行实现。
   *
   * 参数是组件渲染好的两块内容：`search` 是搜索框，`list` 是选项列表与加载/空/
   * 失败三态。宿主可以分别摆放它们，也可以只放其中一块；面板标题、顶部入口、底部
   * 管理入口、说明文字都由宿主用自己的组件、链接和路由渲染。不传时面板只渲染
   * `search` 与 `list`。
   *
   * Everything in the panel except the list, implemented by the host.
   *
   * The argument holds two blocks the component rendered: `search` is the search
   * box and `list` is the option list with its loading, empty and failed states.
   * The host can place them separately or use only one. Panel headings, top
   * entries, bottom management entries and explanatory text are all rendered by
   * the host with its own components, links and router. When omitted the panel
   * renders `search` and `list` alone.
   */
  renderPanel?: (panel: ScopeSwitcherPanelContent) => ReactNode;
  /**
   * 在默认行内容的基础上追加或替换，例如行尾的「更多」菜单、失效态标记。
   *
   * 参数 `content` 是组件渲染好的默认行内容（图标、标签、描述、搜索高亮与选中
   * 标记），`option` 是原始数据。宿主原样使用 `content` 只加行尾，或丢掉它自己
   * 渲染。不传时渲染默认行。
   *
   * 行本身的点击、选中态与键盘交互始终由组件处理。
   *
   * Adds to or replaces the default row content, for example a "more" menu at the
   * row end or an expired marker.
   *
   * `content` is the default row content the component rendered (icon, label,
   * description, search highlight and check mark), and `option` is the raw data.
   * The host reuses `content` and only appends, or discards it and renders its
   * own. The default row renders when omitted.
   *
   * The row's click, selection state and keyboard interaction always stay with
   * the component.
   */
  renderOption?: (content: ReactNode, option: ScopeSwitcherOption) => ReactNode;
  /**
   * 没有可选项时替代列表显示的内容。不传时使用 `labels.empty`。
   * Rendered instead of the list when there are no options. `labels.empty` is
   * used when omitted.
   */
  emptyFallback?: ReactNode;
  /**
   * 加载失败时替代列表显示的内容。不传时使用 `labels.error`。
   * Rendered instead of the list when loading failed. `labels.error` is used
   * when omitted.
   */
  errorFallback?: ReactNode;
  /**
   * 选中一个选项时触发。组件不清空关键字，也不改变面板可见性，由宿主决定。
   * Invoked when an option is selected. The component neither clears the
   * keyword nor changes panel visibility; the host decides.
   */
  onSelect: (option: ScopeSwitcherOption) => void;
  /**
   * 面板是否可见，由宿主持有。
   * Whether the panel is visible, owned by the host.
   */
  visible: boolean;
  /**
   * 面板可见性变化时触发，打开和关闭都会调用，参数是变化后的状态。
   *
   * 宿主在这里写回 `visible`，同时可按需取数，例如打开时才请求第一页选项。
   *
   * Invoked whenever the panel's visibility changes — on both open and close —
   * with the new state.
   *
   * The host writes it back to `visible` and can load data on demand from it,
   * for example requesting the first page only when the panel opens.
   */
  onVisibleChange: (visible: boolean) => void;
  /**
   * 搜索关键字变化时触发。不传时不渲染搜索框。
   * Invoked when the search keyword changes. No search box is rendered when
   * omitted.
   */
  onSearchKeywordChange?: (keyword: string) => void;
  /**
   * 选项列表滚动时触发，参数是滚动后的位置信息。是否加载下一页由宿主判断，加载
   * 完成后把新一页通过 `options` 传回。不传时不响应滚动。
   *
   * Invoked when the option list scrolls, with the resulting scroll metrics. The
   * host decides whether to load the next page and passes it back through
   * `options`. Scrolling is not observed when omitted.
   */
  onScroll?: (metrics: ScopeSwitcherScrollMetrics) => void;
}

/**
 * 组件渲染好的面板内容，作为参数传给 `renderPanel`。
 *
 * Panel content the component rendered, passed to `renderPanel`.
 */
export interface ScopeSwitcherPanelContent {
  /**
   * 搜索框。搜索框不可见时为空节点。
   * The search box; an empty node when it is not shown.
   */
  search: ReactNode;
  /**
   * 选项列表与加载、空、失败三态。
   * The option list with its loading, empty and failed states.
   */
  list: ReactNode;
}

/**
 * 选项列表滚动时的位置信息，由组件从列表元素上读取后传入 `onScroll`。
 *
 * Scroll metrics of the option list, read from the list element and passed to
 * `onScroll`.
 */
export interface ScopeSwitcherScrollMetrics {
  /**
   * 列表已滚动的距离。
   * Distance the list has scrolled.
   */
  scrollTop: number;
  /**
   * 列表内容总高度。
   * Total height of the list content.
   */
  scrollHeight: number;
  /**
   * 列表可视区域高度。
   * Height of the list viewport.
   */
  clientHeight: number;
}

/**
 * 面包屑中的一个层级。按 `kind` 区分固定与可切换两种形态，同一行里可以混用。
 *
 * One level of the breadcrumb. `kind` separates the fixed and selectable forms;
 * both can be mixed in one row.
 */
export type ScopeSwitcherLevel =
  ScopeSwitcherFixedLevel | ScopeSwitcherSelectableLevel;

/**
 * 可替换文案，组件内置英文默认值。
 * Replaceable copy; the component ships English defaults.
 */
export interface ScopeSwitcherLabels {
  /**
   * 搜索框占位文本。
   * Search input placeholder.
   */
  searchPlaceholder?: string;
  /**
   * 没有可选项时的提示。
   * Message shown when there are no options.
   */
  empty?: string;
  /**
   * 有关键字但没有命中项时的提示。不传时回落到 `empty`。
   * Message shown when a keyword matches nothing. Falls back to `empty` when
   * omitted.
   */
  noMatch?: string;
  /**
   * 加载失败时的提示。
   * Message shown when loading failed.
   */
  error?: string;
}

/**
 * 作用域切换器的公开接口。
 *
 * 组件负责面包屑触发器、下拉面板、选项列表与搜索框。面板里除列表以外的内容
 * 由宿主的 `renderPanel` 实现；取数、持久化、权限判断与路由也都由宿主负责。
 *
 * Public interface of the scope switcher.
 *
 * The component owns the breadcrumb triggers, the dropdown panels, the option
 * list and the search box. Everything in a panel except the list is
 * implemented by the host's `renderPanel`; data access, persistence, permission
 * checks and routing belong to the host too.
 */
export interface ScopeSwitcherProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'children' | 'onSelect'
> {
  /**
   * 从左到右渲染的层级，例如库 → 数据空间 → 用户。
   *
   * 层级数量由宿主按当前登录方式决定，少传一层即该层不渲染、不占位：宿主暂时拿
   * 不到某一层的数据时直接省略它，不要传占位项或空层级，组件不提供隐藏开关。
   *
   * Levels rendered left to right, for example library → data space → user.
   *
   * The host decides the level count from the current sign-in method: a level
   * left out is neither rendered nor reserved. Omit a level the host cannot
   * resolve yet rather than passing a placeholder or an empty level; the
   * component has no separate visibility switch.
   */
  levels: readonly ScopeSwitcherLevel[];
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: ScopeSwitcherLabels;
}
