import type { ConsoleProps } from './types.js';

export type {
  ConsoleLabels,
  ConsoleMode,
  ConsoleProps,
  ConsoleSlots,
} from './types.js';

/** 占位组件：渲染一个带模块名的空容器，真实实现随模块 Hook 与组合视图迁入。 */
export function Console({ className, style, labels }: ConsoleProps) {
  return (
    <div className={className} style={style}>
      {labels?.conversation ?? 'Console'}
    </div>
  );
}
