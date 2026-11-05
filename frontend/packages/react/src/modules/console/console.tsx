import { ModulePlaceholder } from '../shared/module-placeholder.js';
import type { ModulePlaceholderProps } from '../shared/types.js';

export type { ModulePlaceholderLabels as ConsoleLabels } from '../shared/types.js';
export type ConsoleProps = ModulePlaceholderProps;

/** Placeholder only; business Hooks and interactions are not implemented yet. */
export function Console(props: ConsoleProps) {
  return <ModulePlaceholder {...props} name="Console" />;
}
