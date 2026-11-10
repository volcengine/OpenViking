import type { Locale } from '../../shared/locale.js';
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
 * 层级的身份：稳定标识、无障碍名与末尾标记。这些值基本不随请求变化。
 *
 * A level's identity: its stable key, accessible name and trailing marker. These
 * values rarely change with a request.
 */
export interface ScopeSwitcherIdentity {
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
   * 触发器末尾的标记，例如套餐名。不传时不渲染。
   * Tag at the end of the trigger, for example a plan name. Not rendered when
   * omitted.
   */
  tag?: ReactNode;
}

/**
 * 触发器展示的内容：当前值文本与图标。
 *
 * What the trigger shows: the current value and its icon.
 */
export interface ScopeSwitcherTrigger {
  /**
   * 触发器上展示的当前选中项文本。不传时按 `data.selectedId` 从 `data.options`
   * 里取对应项的 `label`；两者都没有时触发器为空。
   *
   * 当前值不在已加载的那一页里时，传这一项由宿主给出文本。
   *
   * Text shown on the trigger for the current selection. When omitted it is read
   * from the matching `data.options` entry by `data.selectedId`; with neither the
   * trigger is empty.
   *
   * Supply it when the current value is not on the loaded page.
   */
  label?: string;
  /**
   * 触发器图标，也可以是头像。不传时用选中项的 `icon`。
   * Trigger icon, which can also be an avatar. Falls back to the selected
   * option's `icon` when omitted.
   */
  icon?: ReactNode;
}

/**
 * 层级的数据：可选项与当前选中项。每次请求后都会变。
 *
 * A level's data: the options and the current selection. Changes after every
 * request.
 */
export interface ScopeSwitcherData {
  /**
   * 该层级的权威选项集合，按宿主给定的顺序渲染。它是判断当前选中值是否仍然
   * 有效的依据，因此不要只放当前搜索结果：搜索过滤后的子集请通过
   * `visibleOptions` 传入。
   *
   * The level's authoritative option set, rendered in the order the host gives.
   * It is what decides whether the current selection is still valid, so do not
   * pass only the current search result: hand a filtered subset in through
   * `visibleOptions` instead.
   */
  options: readonly ScopeSwitcherOption[];
  /**
   * 面板里实际渲染的选项。不传时渲染 `options`。搜索过滤、分页追加的下一屏都
   * 走这里，不影响选中值是否有效的判断。
   *
   * Options actually rendered in the panel; `options` is rendered when omitted.
   * Search filtering and an appended page go here and never affect whether the
   * current selection is still valid.
   */
  visibleOptions?: readonly ScopeSwitcherOption[];
  /**
   * 当前选中项 id，由宿主持有。命中项渲染勾选标记并使用
   * `role="option"` + `aria-selected` 表达状态。
   *
   * 组件只按传入的值渲染，不做归一化：id 为空或不在 `options` 里时，列表里没有
   * 任何一行被标记为选中；id 命中一个禁用项时照常标记选中（禁用只阻止交互，不
   * 抹掉选中态），直到宿主把它归一化掉。组件不会替宿主改选中值，也不会回调
   * `actions.onSelect`。
   *
   * 作用域始终要有一个确定的值，因此**归一化由宿主负责**：在数据到达后检查
   * `selectedId` 是否仍有效，无效则改选第一个可选项，并把结果写回 URL 与请求
   * 参数。使用 `ScopeCascade` 时这层已经内置——它的 controller 在权威列表到达
   * 后完成回退、清空下游并加载下一级。
   *
   * 只出现在 `visibleOptions` 之外不算失效：那只说明它被搜索过滤掉了。
   *
   * Selected option id, owned by the host. The matching option renders a check
   * mark and expresses its state with `role="option"` and `aria-selected`.
   *
   * The component renders the value it is given and never normalises it: an id
   * that is empty or absent from `options` marks no row as selected, while an id
   * matching a disabled option is still rendered as selected — disabled blocks
   * interaction, it does not clear the selection — until the host normalises it.
   * The component neither changes the value nor calls `actions.onSelect` on the
   * host's behalf.
   *
   * A scope still needs a definite value, so **normalisation belongs to the
   * host**: after data arrives, check whether `selectedId` is still valid and
   * pick the first selectable option when it is not, then write the result back
   * to the URL and request parameters. `ScopeCascade` already does this — its
   * controller falls back, resets descendants and loads the next level once the
   * authoritative list arrives.
   *
   * Being absent from `visibleOptions` alone does not invalidate it: that only
   * means the current search filtered it out.
   */
  selectedId: string;
  /**
   * 该层级的总条数，用于判断搜索框是否可见。不传时以 `options` 的条数为准。
   *
   * 搜索可见性始终取 `options`、`visibleOptions` 与 `total` 三者的最大值，因此
   * 宿主只传当前一页的 `visibleOptions` 时，只要权威集合超过阈值，搜索框照样
   * 出现。
   *
   * Total number of entries, used to decide whether the search box is shown. The
   * `options` count is used when omitted.
   *
   * Visibility takes the largest of `options`, `visibleOptions` and `total`, so a
   * host that passes only the current page still gets a search box once the
   * authoritative set crosses the threshold.
   */
  total?: number;
}

/**
 * 层级的交互状态。随用户操作与请求进度变化。
 *
 * A level's interaction state. Changes with user actions and request progress.
 */
export interface ScopeSwitcherState {
  /** Keep search visible for hosts that allow entering identities absent from the list. */
  alwaysShowSearch?: boolean;
  /**
   * 选项列表是否正在首次加载。为 `true` 时面板显示加载态，不渲染列表。
   * Whether the option list is loading for the first time. While true the panel
   * shows a loading state instead of the list.
   */
  loading?: boolean;
  /**
   * 选项列表是否加载失败。为 `true` 时面板渲染 `slots.errorFallback`。
   * Whether the option list failed to load. While true the panel renders
   * `slots.errorFallback`.
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
   * 该层级是否暂时不可切换，例如权限尚未就绪。为 `true` 时触发器置灰、不响应
   * 点击，也不渲染下拉。恒为可切换的宿主不传。
   *
   * Whether the level is temporarily not switchable, for example while
   * permissions load. While true the trigger is dimmed, ignores clicks and
   * renders no dropdown. Hosts whose levels are always switchable leave it out.
   */
  disabled?: boolean;
  /**
   * 可选受控展开状态；不传时由本级选择器独立维护。
   * Optional controlled visibility; otherwise local to this selector.
   */
  visible?: boolean;
  /**
   * 当前搜索关键字。非空时搜索框保持可见，并用于高亮命中片段。
   * Current search keyword. A non-empty value keeps the search box visible and
   * drives hit highlighting.
   */
  searchKeyword?: string;
}

/**
 * 层级的插槽：由宿主替换或补充组件渲染的内容。
 *
 * A level's slots: content the host replaces or adds to what the component
 * renders.
 */
export interface ScopeSwitcherSlots {
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
   *
   * The panel keeps its height cap either way: a host layout that needs more room
   * scrolls inside the panel instead of growing it.
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
}

/**
 * 层级的回调：把用户意图交给宿主。
 *
 * A level's callbacks: user intent handed back to the host.
 */
export interface ScopeSwitcherActions {
  /**
   * 选中时触发；组件关闭本级面板，不改变搜索词和业务选中值。
   * Invoked on selection. Closes this panel; selection and search remain host-owned.
   */
  onSelect?: (option: ScopeSwitcherOption) => void;
  /**
   * 面板可见性变化时触发，打开和关闭都会调用，参数是变化后的状态。
   *
   * 宿主在这里写回 `state.visible`，同时可按需取数，例如打开时才请求第一页选项。
   *
   * Invoked whenever the panel's visibility changes — on both open and close —
   * with the new state.
   *
   * The host writes it back to `state.visible` and can load data on demand from
   * it, for example requesting the first page only when the panel opens.
   */
  onVisibleChange?: (visible: boolean) => void;
  /**
   * 搜索关键字变化时触发。不传时不渲染搜索框。
   * Invoked when the search keyword changes. No search box is rendered when
   * omitted.
   */
  onSearchKeywordChange?: (keyword: string) => void;
  /**
   * 选项列表滚动时触发，参数是滚动后的位置信息。是否加载下一页由宿主判断，加载
   * 完成后把新一页通过 `data.options` 传回。不传时不响应滚动。
   *
   * Invoked when the option list scrolls, with the resulting scroll metrics. The
   * host decides whether to load the next page and passes it back through
   * `data.options`. Scrolling is not observed when omitted.
   */
  onScroll?: (metrics: ScopeSwitcherScrollMetrics) => void;
}

/**
 * 组件渲染好的面板内容，作为参数传给 `slots.renderPanel`。
 *
 * Panel content the component rendered, passed to `slots.renderPanel`.
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
 * 选项列表滚动时的位置信息，由组件从列表元素上读取后传入 `actions.onScroll`。
 *
 * Scroll metrics of the option list, read from the list element and passed to
 * `actions.onScroll`.
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
 * 可替换文案，组件内置中英文文案。
 * Replaceable copy with built-in Chinese and English defaults.
 */
export interface ScopeSwitcherLabels {
  /** Loading status text / 加载提示。 */
  loading?: string;
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

/** Layout and language scope: each child owns its state and data subscription. */
export interface ScopeSwitcherProps extends HTMLAttributes<HTMLDivElement> {
  /** 可选文案语言；继承上层，最终默认英文。Optional locale; inherits or defaults to en-US. */
  locale?: Locale;
}

/**
 * 两种层级共有的入参：身份与触发器内容。
 *
 * Props shared by both level forms: identity and what the trigger shows.
 */
export interface ScopeSelectorBaseProps {
  /**
   * 层级身份：稳定标识、无障碍名与末尾标记。
   * Level identity: stable key, accessible name and trailing marker.
   */
  identity: ScopeSwitcherIdentity;
  /** 可选文案语言；继承上层，最终默认英文。Optional locale; inherits or defaults to en-US. */
  locale?: Locale;
  /**
   * 触发器展示的当前值文本与图标。不传时从 `data.options` 里按 `data.selectedId`
   * 取对应项；固定层级没有 `data`，因此固定层级需要在这里给出 `label`。
   *
   * The current value and icon shown on the trigger. When omitted they are read
   * from `data.options` by `data.selectedId`; a fixed level has no `data`, so it
   * supplies `label` here.
   */
  trigger?: ScopeSwitcherTrigger;
  /**
   * 可替换文案，未提供的项使用当前 locale 的内置文案。
   * Replaceable copy; omitted fields follow locale.
   */
  labels?: ScopeSwitcherLabels;
}

/**
 * 固定层级的入参：只展示当前值，不渲染下拉，也不接收任何选项或面板字段。
 *
 * 用于当前作用域不可切换的情形，例如个人终端里跟随登录态固定的库与数据空间、
 * 管理模式下不能切换的数据空间。这类层级在视觉上同样是一条面包屑，只是没有
 * 展开箭头、不可点击，也不需要宿主伪造一份空选项列表。
 *
 * Props of a fixed level: shows the current value only, renders no dropdown and
 * takes no option or panel fields.
 *
 * Used where the current scope cannot be switched, for example a library and
 * data space fixed by the sign-in in a personal terminal, or a data space that
 * cannot be switched in management mode. It still looks like a breadcrumb
 * segment, just without an expand affordance and not clickable, and the host
 * does not have to fabricate an empty option list.
 */
export interface ScopeSelectorFixedProps extends ScopeSelectorBaseProps {
  /**
   * 判别字段，固定层级为 `'fixed'`。
   * Discriminant, always `'fixed'` for a fixed level.
   */
  kind: 'fixed';
}

/**
 * 可切换层级的入参：渲染可展开的触发器与下拉面板。
 *
 * 面板结构、搜索、分页与选中状态全部受控，由宿主传入。同一行里可以只有一个
 * 可切换层级，也可以三个都可切换。
 *
 * Props of a selectable level: renders an expandable trigger and its dropdown
 * panel.
 *
 * The panel structure, search, paging and selection are all controlled and come
 * from the host. A row can hold one selectable level or several.
 */
export interface ScopeSelectorSelectableProps extends ScopeSelectorBaseProps {
  /**
   * 判别字段，可切换层级为 `'selectable'`。
   * Discriminant, always `'selectable'` for a selectable level.
   */
  kind: 'selectable';
  /**
   * 可选项与当前选中项。
   * The options and the current selection.
   */
  data: ScopeSwitcherData;
  /**
   * 加载、错误、忙碌、禁用、展开与搜索关键字等交互状态。
   * Interaction state: loading, error, busy, disabled, visibility and keyword.
   */
  state?: ScopeSwitcherState;
  /**
   * 用户意图回调。
   * Callbacks for user intent.
   */
  actions?: ScopeSwitcherActions;
  /**
   * 宿主替换或补充的渲染内容。
   * Content the host replaces or adds.
   */
  slots?: ScopeSwitcherSlots;
}

/**
 * 一个作用域选择器的入参。按 `kind` 区分固定与可切换两种形态，同一行里可以混用。
 *
 * Props of one scope selector. `kind` separates the fixed and selectable forms;
 * both can be mixed in one row.
 */
export type ScopeSelectorProps =
  ScopeSelectorFixedProps | ScopeSelectorSelectableProps;
