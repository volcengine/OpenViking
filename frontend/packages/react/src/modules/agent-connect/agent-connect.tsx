import { ModulePlaceholder } from '../shared/module-placeholder.js';
import type { ModulePlaceholderProps } from '../shared/types.js';

export type { ModulePlaceholderLabels as AgentConnectLabels } from '../shared/types.js';
export type AgentConnectProps = ModulePlaceholderProps;

/** Placeholder only; business Hooks and interactions are not implemented yet. */
export function AgentConnect(props: AgentConnectProps) {
  return <ModulePlaceholder {...props} name="Agent connection" />;
}
