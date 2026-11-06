import type { ReactNode } from 'react';

/**
 * 编译目标的定时规则，与原生接口的 `update_schedule` 同形：每日或每周的固定
 * 时刻（北京时间）。
 * The schedule rule of a compile target, matching the native
 * `update_schedule`: a daily or weekly clock time in Beijing time.
 */
export type TaskCenterCompileSchedule =
  | {
      frequency: 'daily';
      hour: number;
      minute: number;
    }
  | {
      frequency: 'weekly';
      /** 1 为周一，7 为周日。1 is Monday, 7 is Sunday. */
      weekday: number;
      hour: number;
      minute: number;
    };

/**
 * 数据源导入任务的一行，来自 `/api/v1/watches`。
 * One data-source import task, from `/api/v1/watches`.
 */
export interface TaskCenterImportRow {
  /** 行唯一标识，对应 watch 的 task id。Unique row key, the watch task id. */
  id: string;
  /** 资源名称。Resource name. */
  name: string;
  /** 数据来源类型，例如飞书文档、代码仓库。Source type, for example a Feishu doc or a Git repository. */
  sourceLabel: string;
  /** 原始地址，例如 Git 仓库链接。Original address, for example the Git URL. */
  sourceUrl?: string;
  /** 导入后的 OpenViking 路径。The OpenViking path after import. */
  openVikingPath: string;
  /** 定时同步状态文案，例如「每 1 天」「未开启」。Schedule status copy, for example "every day" or "off". */
  scheduleLabel: string;
  /** 定时同步是否开启。Whether scheduled sync is on. */
  scheduleEnabled: boolean;
  /**
   * 当前的同步间隔，单位分钟。未开启时省略。对应原生 watch 的
   * `watch_interval`。
   *
   * The current sync interval in minutes, omitted when scheduling is off. It
   * matches the native watch's `watch_interval`.
   */
  scheduleIntervalMinutes?: number;
  /** 最近一次执行时间，毫秒时间戳。Last execution time in milliseconds. */
  lastExecutionTime?: number;
  /** 任务创建时间，毫秒时间戳。Creation time in milliseconds. */
  createdAt?: number;
}

/**
 * 知识编译任务的一行，来自编译目标列表。
 * One knowledge-compile task, from the compile target list.
 */
export interface TaskCenterCompileRow {
  /** 行唯一标识，对应编译目标 URI。Unique row key, the compile target URI. */
  id: string;
  /** 目录名称。Directory name. */
  name: string;
  /** 编译目标的完整 URI。Full URI of the compile target. */
  toUri: string;
  /** 状态文案，例如「执行中」「空闲」「未初始化」。Status copy, for example "running", "idle" or "not initialized". */
  statusLabel: string;
  /** 冲突文件数。Number of conflicting files. */
  conflictFileCount: number;
  /** 任务类型，例如首次编译或定时编译。Task type, for example initial or scheduled compile. */
  taskType: string;
  /** 定时更新状态文案，例如「每日 09:30（北京时间）」「未开启」。Schedule copy. */
  scheduleLabel: string;
  /** 定时更新是否开启。Whether scheduled updates are on. */
  scheduleEnabled: boolean;
  /**
   * 当前的定时规则。未开启时省略。与原生编译目标的 `update_schedule` 同形，
   * 宿主改完直接回传即可。
   *
   * The current schedule rule, omitted when scheduling is off. It matches the
   * native compile target's `update_schedule`, so a host can pass it straight
   * back after an edit.
   */
  schedule?: TaskCenterCompileSchedule;
  /** 内容最近更新时间，毫秒时间戳。Last content update in milliseconds. */
  updatedAt?: number;
  /** 任务创建时间，毫秒时间戳。Creation time in milliseconds. */
  createdAt?: number;
}

/** 数据源导入任务的内置列。Built-in columns of the import task list. */
export type TaskCenterImportColumnKey =
  'name' | 'schedule' | 'source' | 'lastExecution' | 'createdAt' | 'path';

/** 知识编译任务的内置列。Built-in columns of the compile task list. */
export type TaskCenterCompileColumnKey =
  | 'name'
  | 'status'
  | 'conflicts'
  | 'taskType'
  | 'schedule'
  | 'updatedAt'
  | 'createdAt'
  | 'path';

/**
 * 一列的配置。省略 `columns` 时使用该列表的全部内置列与默认顺序。
 *
 * A column configuration. Omitting `columns` uses every built-in column of that
 * list in its default order.
 */
export interface TaskCenterColumn<TRow> {
  /**
   * 列标识。内置列用该列表的列 key，自定义列用任意字符串。
   * Column key. A built-in column uses a key of that list; a custom column uses
   * any string.
   */
  key: string;
  /**
   * 覆盖表头文案。省略时使用内置文案或 `labels`。
   * Header override. Omission uses the built-in copy or `labels`.
   */
  title?: ReactNode;
  /** 列宽，单位像素。Column width in pixels. */
  width?: number;
  /** 单元格对齐方式。Cell alignment. */
  align?: 'left' | 'center' | 'right';
  /**
   * 覆盖单元格内容。内置列省略时使用默认渲染；自定义列必须提供。
   * Cell content override. A built-in column falls back to its default
   * rendering; a custom column must provide it.
   */
  render?: (row: TRow) => ReactNode;
}

/**
 * 各页签的列配置。只传需要调整的页签，另一个页签保持默认。
 * Column configuration per tab. Pass only the tab you adjust; the other keeps
 * its default.
 */
export interface TaskCenterColumns {
  import?: readonly TaskCenterColumn<TaskCenterImportRow>[];
  compile?: readonly TaskCenterColumn<TaskCenterCompileRow>[];
}

/** 任意一行的联合类型，供打开任务等回调使用。 */
export type TaskCenterRow = TaskCenterImportRow | TaskCenterCompileRow;
