import type { OvRequestOptions } from '../../client/index.js';
import type { HTMLAttributes, ReactNode } from 'react';

/** 任务状态。Task status. */
export type TaskCenterStatus =
  'pending' | 'running' | 'completed' | 'failed' | 'cancelled';

/** 任务中心模块的插槽。Slots of the task center module. */
export interface TaskCenterSlots {
  /**
   * 列表上方的筛选与工具栏。筛选条件由宿主决定，模块只按宿主给的状态请求。
   * Filters and toolbar above the list. The host decides the filters; the module
   * requests by the state the host gives.
   */
  toolbar?: ReactNode;
  /**
   * 详情区的附加内容，例如执行记录或产物列表。
   * Extra content in the detail area, for example execution records or
   * artifacts.
   */
  detailExtra?: ReactNode;
  /**
   * 空态内容，例如引导导入资源的入口。
   * Empty state, for example an entry that starts importing resources.
   */
  emptyState?: ReactNode;
}

/** 任务中心模块的可替换文案。Replaceable copy of the task center module. */
export interface TaskCenterLabels {
  /** 区域标题。Area title. */
  title?: string;
  /** 没有任务时的提示。Message shown when there are no tasks. */
  empty?: string;
  /** 加载失败提示。Load failure message. */
  error?: string;
  /** 重试按钮文案。Retry button label. */
  retry?: string;
  /** 未选中任务时的提示。Message shown when no task is selected. */
  noSelection?: string;
}

/**
 * 任务中心模块的属性。
 *
 * 模块自己通过 Hook 取数，props 里没有任务数据：宿主给配置、受控的选中任务、
 * 筛选条件、插槽与文案。任务状态归任务数据源，不在模块内复制维护。
 *
 * Props of the task center module.
 *
 * The module loads its own data through a Hook, so no prop carries tasks: the
 * host supplies configuration, the controlled selection, filters, slots and
 * copy. Task state belongs to the task data source and is not duplicated inside
 * the module.
 */
export interface TaskCenterProps extends Omit<
  HTMLAttributes<HTMLDivElement>,
  'onSelect' | 'children'
> {
  /**
   * 受控的选中任务 id。传入时由宿主决定选中项，模块只上报选择意图。
   * The controlled selected task id. When given, the host owns the selection and
   * the module only reports select intent.
   */
  selectedTaskId?: string;
  /**
   * 非受控时的初始选中任务 id。省略时不选中任何任务。
   * Initial selected task id when uncontrolled. Nothing is selected when
   * omitted.
   */
  defaultSelectedTaskId?: string;
  /**
   * 只保留这些状态的任务。省略时不按状态过滤。
   * Keep only tasks in these statuses. No status filter when omitted.
   */
  statuses?: readonly TaskCenterStatus[];
  /**
   * 只保留这些类型的任务，例如 `session_commit`。省略时不按类型过滤。
   * Keep only tasks of these types, for example `session_commit`. No type filter
   * when omitted.
   */
  taskTypes?: readonly string[];
  /**
   * 选中任务变化后触发；清除选中时传入 `null`。
   * Called after the selected task changes; `null` when the selection is
   * cleared.
   */
  onSelectedTaskChange?: (taskId: string | null) => void;
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
