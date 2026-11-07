import type { FileTreeRowContext } from './file-tree-row-context.js';
import { Guides, rowStyle } from './file-tree-row-shared.js';
import type { FileTreeRow } from './types.js';
import { FILE_TREE_ROW_ATTRIBUTE } from './types.js';

/** 非条目行：加载、分页、失败与空态都只占一行，不参与选中。 */
export function FileTreeStateRow({
  row,
  ctx,
}: {
  row: FileTreeRow;
  ctx: FileTreeRowContext;
}) {
  const { text, onLoadMore, onRetry, getRowAttributes, renderSpinner } = ctx;
  // 宿主给了 spinner 插槽就用它，否则用内置的 CSS 圆环。
  const spinner = renderSpinner?.() ?? (
    <span className="ov-file-tree__spinner" />
  );
  const extra = getRowAttributes?.(row) ?? {};
  const {
    className: extraClassName,
    style: extraStyle,
    ...extraAttributes
  } = extra;

  const shell = (children: React.ReactNode, busy = false) => (
    <div
      role="treeitem"
      aria-level={row.depth + 1}
      aria-selected={false}
      aria-busy={busy || undefined}
      {...extraAttributes}
      {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
      className={['ov-file-tree__state-row', extraClassName]
        .filter(Boolean)
        .join(' ')}
      style={{ ...rowStyle(row.depth), ...extraStyle }}
    >
      <Guides depth={row.depth} />
      {children}
    </div>
  );

  switch (row.kind) {
    case 'loading':
      return shell(
        <>
          <span className="ov-file-tree__toggle-spacer" />
          {spinner}
          <span className="ov-file-tree__muted">{text.loading}</span>
        </>,
        true,
      );
    case 'load-more':
      return shell(
        <>
          <span className="ov-file-tree__toggle-spacer" />
          {row.loading ? spinner : null}
          <button
            type="button"
            className="ov-file-tree__action"
            disabled={row.loading || !onLoadMore}
            onClick={() => onLoadMore?.(row)}
          >
            {row.loading ? text.loading : text.loadMore}
          </button>
        </>,
        Boolean(row.loading),
      );
    case 'error':
      return shell(
        <>
          <span className="ov-file-tree__toggle-spacer" />
          <span className="ov-file-tree__muted">
            {row.message ?? text.error}
          </span>
          <button
            type="button"
            className="ov-file-tree__action"
            disabled={!onRetry}
            onClick={() => onRetry?.(row)}
          >
            {text.retry}
          </button>
        </>,
      );
    case 'empty':
      return shell(
        <>
          <span className="ov-file-tree__toggle-spacer" />
          <span className="ov-file-tree__muted">{text.empty}</span>
        </>,
      );
    default:
      return null;
  }
}
