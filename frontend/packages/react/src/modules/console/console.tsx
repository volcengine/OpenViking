import { ModulePlaceholder } from '../shared/module-placeholder.js';
import type { ConsoleProps } from './types.js';

export type {
  ConsoleLabels,
  ConsoleMode,
  ConsoleProps,
  ConsoleSlots,
} from './types.js';

/**
 * 占位组件：只渲染模块名与占位文案，其余 props 已定型但尚未消费。真实实现
 * 随模块 Hook 与组合视图迁入。
 * Placeholder: renders the module name and placeholder copy only. The remaining
 * props are settled but not consumed yet; the real implementation arrives with
 * the module Hook and composite view.
 */
export function Console({ className, style, labels }: ConsoleProps) {
  return (
    <ModulePlaceholder
      className={className}
      style={style}
      name="Console"
      labels={{ title: labels?.conversation }}
    />
  );
}
