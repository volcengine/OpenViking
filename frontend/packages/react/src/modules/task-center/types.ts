import type { OvRequestOptions } from '../../client/index.js';
import type { HTMLAttributes, ReactNode } from 'react';

import type {
  TaskCenterColumns,
  TaskCenterCompileRow,
  TaskCenterCompileSchedule,
  TaskCenterImportRow,
} from './columns.js';

/**
 * 任务管理的页签。数据源导入对应定时同步的 watch，知识编译对应编译目标。
 * Tabs of the task center. Data-source import covers scheduled watches; knowledge
 * compile covers compile targets.
 */
export type TaskCenterTab = 'import' | 'compile';

/**
 * 页签的可见范围。「其他成员」只在能查看账号下全部任务时出现，由模块从当前
 * 身份判断，不需要宿主传开关。
 *
 * Visibility scope of a tab. "Others" appears only when the current identity can
 * see every task in the account; the module decides that from the current
 * identity, so the host passes no switch.
 */
export type TaskCenterScope = 'mine' | 'others';

/** 任务中心模块的插槽。Slots of the task center module. */
export interface TaskCenterSlots {
  /**
   * 列表上方的工具栏，例如新建任务、搜索。筛选在表头由模块提供。
   * Toolbar above the list, for example a create action or search. The module
   * provides filtering in the table header.
   */
  toolbar?: ReactNode;
  /**
   * 空态内容，例如引导导入资源的入口。
   * Empty state, for example an entry that starts importing resources.
   */
  emptyState?: ReactNode;
}

/** 任务中心模块的可替换文案。Replaceable copy of the task center module. */
export interface TaskCenterLabels {
  /**
   * 占位说明。缺省为 `Placeholder. Functionality is not available yet.`。
   * Placeholder description. Defaults to "Placeholder. Functionality is not
   * available yet.".
   */
  description?: string;
  /** 区域标题。Area title. */
  title?: string;
  /** 数据源导入页签名称。Label of the import tab. */
  importTab?: string;
  /** 知识编译页签名称。Label of the compile tab. */
  compileTab?: string;
  /** 「我的任务」范围名称。Label of the "my tasks" scope. */
  mineScope?: string;
  /** 「其他成员任务」范围名称。Label of the "others' tasks" scope. */
  othersScope?: string;
  /** 没有任务时的提示。Message shown when there are no tasks. */
  empty?: string;
  /** 加载失败提示。Load failure message. */
  error?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
  /** 定时同步列的表头。Header of the scheduled-sync column. */
  scheduleColumn?: string;
  /** 定时同步开启状态文案。Copy for an enabled schedule. */
  scheduleEnabled?: string;
  /** 定时同步关闭状态文案。Copy for a disabled schedule. */
  scheduleDisabled?: string;
  /** 编辑定时周期的按钮文案。Label of the control that edits a schedule. */
  editSchedule?: string;
  /** 定时保存失败时的提示。Message shown when saving a schedule fails. */
  saveFailed?: string;
}

/**
 * 任务中心模块的属性。
 *
 * 模块自己通过 Hook 取数，props 里没有任务数据：宿主给配置、受控状态、回调、
 * 列配置、插槽与文案。两个页签各有自己的数据源与列，页签内筛选由模块维护。
 *
 * 能查看「其他成员任务」取决于当前身份，模块自行判断，宿主不需要传权限开关。
 *
 * Props of the task center module.
 *
 * The module loads its own data through a Hook, so no prop carries tasks: the
 * host supplies configuration, controlled state, callbacks, column
 * configuration, slots and copy. Each tab has its own data source and columns,
 * and the module maintains filtering inside a tab.
 *
 * Whether "others' tasks" is available depends on the current identity; the
 * module decides that itself, so the host passes no permission switch.
 */
export interface TaskCenterProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onSelect' | 'children'
> {
  /**
   * 受控的当前页签。传入时由宿主决定页签，模块只上报切换意图。
   * The controlled tab. When given, the host owns the tab and the module only
   * reports switch intent.
   */
  tab?: TaskCenterTab;
  /**
   * 非受控时的初始页签，缺省为 `import`。
   * Initial tab when uncontrolled; defaults to `import`.
   */
  defaultTab?: TaskCenterTab;
  /**
   * 受控的页签可见范围。传入时由宿主决定范围，模块只上报切换意图。
   * The controlled scope. When given, the host owns it and the module only
   * reports switch intent.
   */
  scope?: TaskCenterScope;
  /**
   * 非受控时的初始范围，缺省为 `mine`。
   * Initial scope when uncontrolled; defaults to `mine`.
   */
  defaultScope?: TaskCenterScope;
  /**
   * 列配置。省略时两个页签都用各自的内置列与默认顺序。
   * Column configuration. Omission uses each tab's built-in columns in their
   * default order.
   */
  columns?: TaskCenterColumns;
  /**
   * 页签变化后触发。
   * Called after the tab changes.
   */
  onTabChange?: (tab: TaskCenterTab) => void;
  /**
   * 可见范围变化后触发。
   * Called after the scope changes.
   */
  onScopeChange?: (scope: TaskCenterScope) => void;
  /**
   * 打开资源时触发，例如点击导入任务的目标路径。任务详情由模块内部以抽屉展示，
   * 不需要宿主参与。
   * Called when a resource is opened, for example the target path of an import
   * task. Task details are shown by the module in a drawer, so the host takes no
   * part in that.
   */
  onOpenResource?: (uri: string) => void;
  /**
   * 导入任务的同步间隔保存。返回的 Promise 成功即视为已写入，模块随后重新取数；
   * 失败时保留编辑内容并展示错误，错误文案取 `labels.saveFailed`。
   *
   * Saving the sync interval of an import task. A resolved Promise means the
   * write succeeded and the module reloads; a rejection keeps the edit and shows
   * the error from `labels.saveFailed`.
   */
  onSaveScheduleInterval?: (
    row: TaskCenterImportRow,
    intervalMinutes: number,
  ) => Promise<void>;
  /**
   * 编译目标的定时规则保存。参数与原生 `update_schedule` 同形，宿主可直接转发。
   * 成功与失败的处理同 {@link onSaveScheduleInterval}。
   *
   * Saving the schedule of a compile target. The argument matches the native
   * `update_schedule`, so a host can forward it as-is. Success and failure are
   * handled the same way as {@link onSaveScheduleInterval}.
   */
  onSaveCompileSchedule?: (
    row: TaskCenterCompileRow,
    schedule: TaskCenterCompileSchedule | null,
  ) => Promise<void>;
  /**
   * 插槽，见 {@link TaskCenterSlots}。
   * Slots, see {@link TaskCenterSlots}.
   */
  slots?: TaskCenterSlots;
  /**
   * 可替换文案，缺省为英文。
   * Replaceable copy; English by default.
   */
  labels?: TaskCenterLabels;
  /**
   * 请求配置覆盖，逐字段覆盖最近的 Provider。
   * Request configuration override, applied field by field over the nearest
   * Provider.
   */
  requestOptions?: OvRequestOptions;
}

export type {
  TaskCenterColumn,
  TaskCenterColumns,
  TaskCenterCompileColumnKey,
  TaskCenterCompileRow,
  TaskCenterCompileSchedule,
  TaskCenterImportColumnKey,
  TaskCenterImportRow,
  TaskCenterRow,
} from './columns.js';
