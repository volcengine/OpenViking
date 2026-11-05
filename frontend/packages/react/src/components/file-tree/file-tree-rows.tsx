import type { ReactNode } from 'react';

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

interface StateRowProps {
  row: FileTreeRow;
  ctx: FileTreeRowContext;
  /** 行自身的附加属性，例如 `aria-busy` 与色调标记。 */
  attributes?: Record<string, unknown>;
  tone?: 'error';
  children: ReactNode;
}

/**
 * 状态行外壳：与条目行一致地应用 `getRowAttributes`，让宿主能给加载、分页、
 * 失败与空态行追加高亮或埋点属性。
 */
function StateRow({ row, ctx, attributes, tone, children }: StateRowProps) {
  const {
    className: extraClassName,
    style: extraStyle,
    ...extraAttributes
  } = ctx.getRowAttributes?.(row) ?? {};

  return (
    <div
      role="treeitem"
      aria-level={row.depth + 1}
      aria-selected={false}
      {...attributes}
      {...extraAttributes}
      {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
      className={[styles.childRow, extraClassName].filter(Boolean).join(' ')}
      data-tone={tone}
      style={{ ...rowIndent(row), ...extraStyle }}
    >
      {children}
    </div>
  );
}

/** 非条目行：加载中、加载更多、失败与空态都只占一行，不参与选中。 */
function ChildRow({ row, ctx }: { row: FileTreeRow; ctx: FileTreeRowContext }) {
  const { text, onLoadMore, onRetry } = ctx;

  switch (row.kind) {
    case 'loading':
      return (
        <StateRow row={row} ctx={ctx} attributes={{ 'aria-busy': true }}>
          <span className={styles.toggleSpacer} />
          <Spinner />
          <span className={styles.muted}>{text.loading}</span>
        </StateRow>
      );
    case 'load-more':
      return (
        <StateRow row={row} ctx={ctx}>
          <button
            type="button"
            className={styles.childAction}
            disabled={row.loading || !onLoadMore}
            onClick={() => onLoadMore?.(row)}
          >
            {row.loading ? <Spinner /> : null}
            {text.loadMore}
          </button>
        </StateRow>
      );
    case 'error':
      return (
        <StateRow row={row} ctx={ctx} tone="error">
          <span className={styles.muted}>{row.message ?? text.error}</span>
          <button
            type="button"
            className={styles.childAction}
            disabled={!onRetry}
            onClick={() => onRetry?.(row)}
          >
            {text.retry}
          </button>
        </StateRow>
      );
    case 'empty':
      return (
        <StateRow row={row} ctx={ctx}>
          <span className={styles.muted}>{text.empty}</span>
        </StateRow>
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
