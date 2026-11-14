import type { ReactNode } from 'react';
import { Empty } from '../../shared/primitives/empty/empty.js';

import { FileTreeRowView } from './file-tree-entry-row.js';
import { FileTreeStateRow } from './file-tree-state-row.js';
import type {
  FileTreeRowContext,
  ResolvedLabels,
} from './file-tree-row-context.js';
import type { FileTreeErrorRow, FileTreeRow } from './types.js';

/** 整树级失败：只有一个错误行且没有归属父节点。 */
function isWholeTreeError(rows: readonly FileTreeRow[]) {
  if (rows.length !== 1) return false;
  const [row] = rows;
  return row?.kind === 'error' && !row.parentId;
}

export interface FileTreeBodyProps {
  rows: readonly FileTreeRow[];
  isReady: boolean;
  text: ResolvedLabels;
  loadingFallback?: ReactNode;
  errorFallback?: ReactNode;
  emptyFallback?: ReactNode;
  ctx: FileTreeRowContext;
}

/**
 * 树内容区：首次加载、整树失败、空态与正常行列表。
 *
 * The tree body: first load, whole-tree failure, empty state, and the row list.
 */
export function FileTreeBody({
  rows,
  isReady,
  text,
  loadingFallback,
  errorFallback,
  emptyFallback,
  ctx,
}: FileTreeBodyProps) {
  if (!isReady) {
    return (
      <div className="ov-file-tree__state">
        {loadingFallback ?? (
          <span className="ov-file-tree__muted">{text.loading}</span>
        )}
      </div>
    );
  }

  if (isWholeTreeError(rows)) {
    const message = (rows[0] as FileTreeErrorRow).message ?? text.error;
    return (
      <div className="ov-file-tree__state">
        {errorFallback ?? (
          <span className="ov-file-tree__muted">{message}</span>
        )}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className="ov-file-tree__state">
        {emptyFallback ?? (
          <Empty size="compact" illustration="folder" title={text.empty} />
        )}
      </div>
    );
  }

  return (
    <div role="group" className="ov-file-tree__rows">
      {rows.map((row) =>
        row.kind === 'entry' ? (
          <FileTreeRowView key={row.id} row={row} ctx={ctx} />
        ) : (
          <FileTreeStateRow key={row.id} row={row} ctx={ctx} />
        ),
      )}
    </div>
  );
}
