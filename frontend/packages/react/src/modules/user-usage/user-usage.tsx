import { ModulePlaceholder } from '../shared/module-placeholder.js';
import type { ModulePlaceholderProps } from '../shared/types.js';

export type { ModulePlaceholderLabels as UserUsageLabels } from '../shared/types.js';
export type UserUsageProps = ModulePlaceholderProps;

/** Placeholder only; business Hooks and interactions are not implemented yet. */
export function UserUsage(props: UserUsageProps) {
  return <ModulePlaceholder {...props} name="My usage" />;
}
