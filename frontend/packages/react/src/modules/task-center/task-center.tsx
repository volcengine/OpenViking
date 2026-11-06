import type { TaskCenterProps } from './types.js';

export type {
  TaskCenterColumn,
  TaskCenterColumns,
  TaskCenterCompileColumnKey,
  TaskCenterCompileRow,
  TaskCenterCompileSchedule,
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
    <div
      className={['ov-module-placeholder', className].filter(Boolean).join(' ')}
      style={style}
    >
      <p className="ov-module-placeholder__title">
        {labels?.title ?? 'Task center'}
      </p>
      <p className="ov-module-placeholder__description">
        {labels?.description ??
          'Placeholder. Functionality is not available yet.'}
      </p>
    </div>
  );
}
