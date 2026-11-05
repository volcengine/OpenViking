import { ModulePlaceholder } from '../shared/module-placeholder.js';
import type { ModulePlaceholderProps } from '../shared/types.js';

export type { ModulePlaceholderLabels as FileBrowserLabels } from '../shared/types.js';
export type FileBrowserProps = ModulePlaceholderProps;

/** Placeholder only; business Hooks and interactions are not implemented yet. */
export function FileBrowser(props: FileBrowserProps) {
  return <ModulePlaceholder {...props} name="File browser" />;
}
