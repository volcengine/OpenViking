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

/** 占位组件：只渲染固定尺寸的空壳，真实视图按 types.ts 的契约重写。 */
export function FileTree() {
  return <div className="ov-file-tree">FileTree</div>;
}
