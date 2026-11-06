import type { TaskCenterProps } from './types.js';

export type {
  TaskCenterColumn,
  TaskCenterColumns,
  TaskCenterCompileColumnKey,
  TaskCenterCompileRow,
  TaskCenterImportColumnKey,
  TaskCenterImportRow,
  TaskCenterLabels,
  TaskCenterProps,
  TaskCenterRow,
  TaskCenterScope,
  TaskCenterSlots,
  TaskCenterTab,
} from './types.js';

/** 占位组件：渲染一个带模块名的空容器，真实实现随模块 Hook 与组合视图迁入。 */
export function TaskCenter({ className, style, labels }: TaskCenterProps) {
  return (
    <div className={className} style={style}>
      {labels?.title ?? 'Task center'}
    </div>
  );
}
