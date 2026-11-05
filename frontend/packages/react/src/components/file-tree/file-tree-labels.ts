import type { FileTreeLabels } from './types.js';

/** 组件内置文案，宿主可通过 `labels` 逐项覆盖。 */
export const DEFAULT_LABELS: Required<FileTreeLabels> = {
  empty: 'No files yet',
  error: 'Failed to load',
  retry: 'Retry',
  loadMore: 'Load more',
  loading: 'Loading…',
  expand: 'Expand {name}',
  collapse: 'Collapse {name}',
};

/** 用节点名替换文案里的 `{name}` 占位。 */
export function fillName(template: string, name: string) {
  return template.replace('{name}', name);
}
