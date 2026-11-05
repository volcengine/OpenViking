import type { KeyboardEvent, ReactNode } from 'react';
import type { HTMLAttributes } from 'react';

import type {
  FileTreeEntryRow,
  FileTreeErrorRow,
  FileTreeLabels,
  FileTreeLoadMoreRow,
  FileTreeRow,
} from './types.js';

/** 渲染行所需的外部输入，由组件主体注入。 */
export interface FileTreeRowContext {
  text: Required<FileTreeLabels>;
  selectedId?: string | null;
  tooltipRowId: string | null;
  getRowAttributes?: (row: FileTreeRow) => HTMLAttributes<HTMLDivElement>;
  renderDashboard?: (row: FileTreeEntryRow) => ReactNode;
  renderRowTooltip?: (row: FileTreeEntryRow) => ReactNode;
  onSelect?: (row: FileTreeEntryRow) => void;
  onToggleExpand?: (row: FileTreeEntryRow) => void;
  onLoadMore?: (row: FileTreeLoadMoreRow) => void;
  onRetry?: (row: FileTreeErrorRow) => void;
  onOpenRetentionSettings?: (uri: string) => void;
  onRowKeyDown: (
    event: KeyboardEvent<HTMLDivElement>,
    row: FileTreeEntryRow,
  ) => void;
  onRowEnter: (rowId: string) => void;
  onRowLeave: () => void;
}

/** 行按深度缩进，其余内边距由样式负责。 */
export function rowIndent(row: FileTreeRow) {
  return { paddingLeft: `calc(${row.depth} * 16px)` };
}
