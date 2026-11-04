import { FileTreeEntryRowView } from './file-tree-entry-row.js';
import styles from './file-tree.module.css';
import { Spinner } from './file-tree-icons.js';
import { rowIndent, type FileTreeRowContext } from './file-tree-row-context.js';
import { FILE_TREE_ROW_ATTRIBUTE, type FileTreeRow } from './types.js';

/**
 * 整树级失败：只有一个错误行且没有归属父节点。
 * Whole-tree failure: a single error row that belongs to no parent.
 */
export function isWholeTreeError(rows: readonly FileTreeRow[]) {
  if (rows.length !== 1) return false;
  const [row] = rows;
  return row?.kind === 'error' && !row.parentId;
}

/** 非条目行：加载中、加载更多、失败与空态都只占一行，不参与选中。 */
function ChildRow({ row, ctx }: { row: FileTreeRow; ctx: FileTreeRowContext }) {
  const { text, onLoadMore, onRetry } = ctx;

  switch (row.kind) {
    case 'loading':
      return (
        <div
          role="treeitem"
          aria-level={row.depth + 1}
          aria-selected={false}
          aria-busy="true"
          {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
          className={styles.childRow}
          style={rowIndent(row)}
        >
          <span className={styles.toggleSpacer} />
          <Spinner />
          <span className={styles.muted}>{text.loading}</span>
        </div>
      );
    case 'load-more':
      return (
        <div
          role="treeitem"
          aria-level={row.depth + 1}
          aria-selected={false}
          {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
          className={styles.childRow}
          style={rowIndent(row)}
        >
          <button
            type="button"
            className={styles.childAction}
            disabled={row.loading}
            onClick={() => onLoadMore?.(row)}
          >
            {row.loading ? <Spinner /> : null}
            {text.loadMore}
          </button>
        </div>
      );
    case 'error':
      return (
        <div
          role="treeitem"
          aria-level={row.depth + 1}
          aria-selected={false}
          {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
          className={styles.childRow}
          data-tone="error"
          style={rowIndent(row)}
        >
          <span className={styles.muted}>{row.message ?? text.error}</span>
          <button
            type="button"
            className={styles.childAction}
            onClick={() => onRetry?.(row)}
          >
            {text.retry}
          </button>
        </div>
      );
    case 'empty':
      return (
        <div
          role="treeitem"
          aria-level={row.depth + 1}
          aria-selected={false}
          {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
          className={styles.childRow}
          style={rowIndent(row)}
        >
          <span className={styles.muted}>{text.empty}</span>
        </div>
      );
    default:
      return <FileTreeEntryRowView row={row} ctx={ctx} />;
  }
}

/** 按行类型分派渲染，条目行与状态行分别处理。 */
export function FileTreeRowView({
  row,
  ctx,
}: {
  row: FileTreeRow;
  ctx: FileTreeRowContext;
}) {
  return <ChildRow row={row} ctx={ctx} />;
}
