import { ModulePlaceholder } from '../shared/module-placeholder.js';
import type { ModulePlaceholderProps } from '../shared/types.js';

export type { ModulePlaceholderLabels as TaskCenterLabels } from '../shared/types.js';
export type TaskCenterProps = ModulePlaceholderProps;

/** Placeholder only; business Hooks and interactions are not implemented yet. */
export function TaskCenter(props: TaskCenterProps) {
  return <ModulePlaceholder {...props} name="Task center" />;
}
