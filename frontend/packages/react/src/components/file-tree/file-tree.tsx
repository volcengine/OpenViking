import type { FileTreeProps } from './types.js';

export { FileTreeSpinner } from './file-tree-spinner.js';
export {
  resolveFileTreeInfoKey,
  showsInlineDescription,
  splitVikingUri,
} from './file-tree-info.js';
export {
  DEFAULT_INFO_LABELS,
  resolveInfoCopy,
} from './file-tree-info-labels.js';
export { FILE_TREE_ROW_ATTRIBUTE } from './types.js';
export type {
  FileTreeChildRow,
  FileTreeComponent,
  FileTreeEmptyRow,
  FileTreeEntryRow,
  FileTreeErrorRow,
  FileTreeHandle,
  FileTreeInfoKey,
  FileTreeInfoLabels,
  FileTreeLabels,
  FileTreeLoadingRow,
  FileTreeLoadMoreRow,
  FileTreeOperations,
  FileTreeProps,
  FileTreeRow,
  FileTreeRowBase,
  FileTreeSlots,
} from './types.js';

/**
 * 占位组件：只渲染固定尺寸的空壳，真实视图按 types.ts 的契约重写。
 * 保留 `FileTreeProps` 签名，消费者可以照文档里的用法写 JSX。
 *
 * Placeholder: renders a fixed shell until the real view lands against the
 * contract in types.ts. It keeps the `FileTreeProps` signature so consumers can
 * write the JSX the docs show.
 */
export function FileTree(_props: FileTreeProps) {
  return <div className="ov-file-tree">FileTree</div>;
}
