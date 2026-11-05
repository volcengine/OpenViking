import type { ReactNode } from 'react';

import type { FileTreeRowContext } from './file-tree-row-context.js';
import { FileTreeRowView, isWholeTreeError } from './file-tree-rows.js';
import styles from './file-tree.module.css';
import type { FileTreeErrorRow, FileTreeLabels, FileTreeRow } from './types.js';

export interface FileTreeBodyProps {
  rows: readonly FileTreeRow[];
  isReady: boolean;
  text: Required<FileTreeLabels>;
  loadingFallback?: ReactNode;
  errorFallback?: ReactNode;
  emptyFallback?: ReactNode;
  ctx: FileTreeRowContext;
}

/** 树内容区：加载中、整树失败、空态与正常行列表。 */
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
      <div className={styles.state}>
        {loadingFallback ?? (
          <span className={styles.muted}>{text.loading}</span>
        )}
      </div>
    );
  }

  if (isWholeTreeError(rows)) {
    const message = (rows[0] as FileTreeErrorRow).message ?? text.error;
    return (
      <div className={styles.state}>
        {errorFallback ?? <span className={styles.muted}>{message}</span>}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <div className={styles.state}>
        {emptyFallback ?? <span className={styles.muted}>{text.empty}</span>}
      </div>
    );
  }

  return (
    <div role="group" className={styles.rows}>
      {rows.map((row) => (
        <FileTreeRowView key={row.id} row={row} ctx={ctx} />
      ))}
    </div>
  );
}
