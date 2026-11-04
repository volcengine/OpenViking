import type { HTMLAttributes, ReactNode } from 'react';

/**
 * 一个层级中的可选项。宿主把自己的数据空间、账号或用户记录映射成该结构。
 *
 * One selectable value in a scope level. Hosts map their own workspace,
 * account or user records onto this shape.
 */
export interface ScopeOption {
  /**
   * 选项唯一标识。
   * Unique option key.
   */
  id: string;
  /**
   * 选项显示文本。
   * Option label.
   */
  label: string;
  /**
   * 显示在标签下方的次要说明。
   * Secondary line under the label.
   */
  description?: string;
  /**
   * 是否禁用该选项。
   * Whether the option is disabled.
   */
  disabled?: boolean;
  /**
   * 尾部标记，例如角色或套餐。
   * Trailing tag, for example a role or plan.
   */
  badge?: ReactNode;
}

/** 层级内的一个动作，例如新建或删除。One action inside a level, for example create or delete. */
export interface ScopeLevelAction {
  /**
   * 动作唯一标识。
   * Unique action key.
   */
  key: string;
  /**
   * 动作显示文本。
   * Action label.
   */
  label: string;
  /**
   * 动作图标。
   * Action icon.
   */
  icon?: ReactNode;
  /**
   * 是否以危险样式渲染，例如删除。
   * Renders the action in a destructive style, for example delete.
   */
  destructive?: boolean;
  /**
   * 是否禁用该动作。
   * Whether the action is disabled.
   */
  disabled?: boolean;
  /**
   * 选中该动作时触发。
   * Invoked when the action is selected.
   */
  onSelect: () => void;
}

/**
 * 一行中的一个切换器。每个层级各自持有搜索与分页状态，全部由宿主持有，
 * 因此同一个组件可以服务数据空间、账号或用户选择，而无需知道选的是什么。
 *
 * One switcher in the row. A level owns its own search and paging state and the
 * host keeps all of it, so the same component serves a workspace, account or
 * user picker without knowing what it selects.
 */
export interface ScopeSwitcherLevel {
  /**
   * 稳定标识，例如 `workspace`、`account` 或 `user`。
   * Stable key, for example `workspace`, `account` or `user`.
   */
  key: string;
  /**
   * 触发器的无障碍名称。
   * Accessible name of the trigger.
   */
  label: string;
  /**
   * 触发器上展示的当前选中项文本。
   * Text shown on the trigger for the current selection.
   */
  valueLabel: string;
  /**
   * 触发器图标。
   * Trigger icon.
   */
  icon?: ReactNode;
  /**
   * 可选项列表。
   * Selectable options.
   */
  options: readonly ScopeOption[];
  /**
   * 当前选中项 id，由宿主持有。
   * Selected option id, owned by the host.
   */
  selectedId?: string | null;
  /**
   * 选项列表是否正在首次加载。
   * Whether the option list is loading for the first time.
   */
  loading?: boolean;
  /**
   * 是否正在加载下一页选项。
   * Whether the next page of options is loading.
   */
  loadingMore?: boolean;
  /**
   * 是否还有更多选项可以加载。
   * Whether more options can be loaded.
   */
  hasMore?: boolean;
  /**
   * 当前搜索关键字，层级可搜索时使用。
   * Current search keyword, when the level is searchable.
   */
  searchKeyword?: string;
  /**
   * 该层级是否支持搜索。
   * Whether the level is searchable.
   */
  searchable?: boolean;
  /**
   * 固定作用域时直接展示值，不渲染下拉。
   * Renders the value without a dropdown, for a fixed scope.
   */
  readOnly?: boolean;
  /**
   * 是否禁用该层级。
   * Whether the level is disabled.
   */
  disabled?: boolean;
  /**
   * 选项加载失败时的提示。
   * Shown when the option list failed to load.
   */
  errorMessage?: string;
  /**
   * 下拉底部的入口，例如新建或编辑。
   * Entries at the bottom of the dropdown, for example create or edit.
   */
  actions?: readonly ScopeLevelAction[];
  /**
   * 选中一个选项时触发。
   * Invoked when an option is selected.
   */
  onSelect: (option: ScopeOption) => void;
  /**
   * 搜索关键字变化时触发。
   * Invoked when the search keyword changes.
   */
  onSearchChange?: (keyword: string) => void;
  /**
   * 加载下一页选项时触发。
   * Invoked when the next page of options should be loaded.
   */
  onLoadMore?: () => void;
  /**
   * 重新加载失败的选项列表。
   * Retries a failed option load.
   */
  onRetry?: () => void;
}

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
   * 无可选项时的提示。
   * Message shown when there are no options.
   */
  empty?: string;
  /**
   * 加载失败时的提示。
   * Message shown when loading failed.
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
   * 动作菜单的无障碍名称。
   * Accessible name of the actions menu.
   */
  actions?: string;
  /**
   * 设置入口文案。
   * Settings entry label.
   */
  settings?: string;
}

/** 作用域切换器的公开接口。Public interface of the scope switcher. */
export interface ScopeSwitcherProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onSelect'
> {
  /**
   * 从左到右渲染的层级，例如数据空间 → 账号 → 用户。
   * Levels rendered left to right, for example workspace → account → user.
   */
  levels: readonly ScopeSwitcherLevel[];
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: ScopeSwitcherLabels;
  /**
   * 打开宿主自有的当前作用域设置页。
   * Opens the host-owned settings page for the current scope.
   */
  onOpenSettings?: () => void;
  /**
   * 尾部插槽，用于宿主自有控件。
   * Trailing slot for host-specific controls.
   */
  children?: ReactNode;
}
