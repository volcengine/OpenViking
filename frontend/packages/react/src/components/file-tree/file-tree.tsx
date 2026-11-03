import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import type { KeyboardEvent, MouseEvent, ReactNode } from 'react';

import styles from './file-tree.module.css';
import {
  FILE_TREE_ROW_ATTRIBUTE,
  type FileTreeComponent,
  type FileTreeEntryRow,
  type FileTreeErrorRow,
  type FileTreeHandle,
  type FileTreeLoadMoreRow,
  type FileTreeProps,
  type FileTreeRow,
} from './types.js';

export { FILE_TREE_ROW_ATTRIBUTE } from './types.js';
export type {
  FileTreeChildRow,
  FileTreeComponent,
  FileTreeEmptyRow,
  FileTreeEntryRow,
  FileTreeErrorRow,
  FileTreeHandle,
  FileTreeLabels,
  FileTreeLoadingRow,
  FileTreeLoadMoreRow,
  FileTreeProps,
  FileTreeRow,
  FileTreeRowBase,
  FileTreeSlots,
} from './types.js';

const DEFAULT_LABELS = {
  empty: 'No files yet',
  error: 'Failed to load',
  retry: 'Retry',
  loadMore: 'Load more',
  loading: 'Loading…',
  expand: 'Expand {name}',
  collapse: 'Collapse {name}',
} as const;

/** 用节点名替换文案里的 `{name}` 占位。 */
function fillName(template: string, name: string) {
  return template.replace('{name}', name);
}

/**
 * 整树级失败：只有一个错误行且没有归属父节点。
 * Whole-tree failure: a single error row that belongs to no parent.
 */
function isWholeTreeError(rows: readonly FileTreeRow[]) {
  if (rows.length !== 1) return false;
  const [row] = rows;
  return row?.kind === 'error' && !row.parentId;
}

function Chevron({ expanded }: { expanded: boolean }) {
  return (
    <svg
      aria-hidden="true"
      className={styles.chevron}
      data-expanded={expanded || undefined}
      viewBox="0 0 16 16"
      width="12"
      height="12"
    >
      <path
        d="M6 3.5 10.5 8 6 12.5"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="1.5"
      />
    </svg>
  );
}

function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg
      aria-hidden="true"
      className={styles.icon}
      viewBox="0 0 16 16"
      width="14"
      height="14"
    >
      <path
        d={
          open
            ? 'M1.5 13V4.5A1 1 0 0 1 2.5 3.5h3l1.2 1.5h5.8a1 1 0 0 1 1 1V7M1.5 13l1.6-5h11.4l-1.6 5H1.5Z'
            : 'M1.5 12.5v-9a1 1 0 0 1 1-1h3l1.2 1.5h6.8a1 1 0 0 1 1 1v7.5a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1Z'
        }
        fill="none"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.2"
      />
    </svg>
  );
}

function FileIcon() {
  return (
    <svg
      aria-hidden="true"
      className={styles.icon}
      viewBox="0 0 16 16"
      width="14"
      height="14"
    >
      <path
        d="M4 1.5h5L12.5 5v9.5h-8.5V1.5Z"
        fill="none"
        stroke="currentColor"
        strokeLinejoin="round"
        strokeWidth="1.2"
      />
      <path
        d="M9 1.5V5h3.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
      />
    </svg>
  );
}

function Spinner() {
  return <span aria-hidden="true" className={styles.spinner} />;
}

export const FileTree: FileTreeComponent = forwardRef<
  FileTreeHandle,
  FileTreeProps
>(function FileTree(
  {
    rows,
    selectedId,
    isReady = true,
    loadingFallback,
    errorFallback,
    emptyFallback,
    slots,
    labels,
    getRowAttributes,
    renderDashboard,
    renderRowTooltip,
    onSelect,
    onToggleExpand,
    onLoadMore,
    onRetry,
    onOpenRetentionSettings,
    className,
    ...rest
  },
  ref,
) {
  const rootRef = useRef<HTMLDivElement>(null);
  const [tooltipRowId, setTooltipRowId] = useState<string | null>(null);
  const tooltipTimerRef = useRef<number | undefined>(undefined);

  const text = { ...DEFAULT_LABELS, ...labels };

  useEffect(
    () => () => {
      if (tooltipTimerRef.current !== undefined) {
        window.clearTimeout(tooltipTimerRef.current);
      }
    },
    [],
  );

  const queryRowElements = useCallback(() => {
    const root = rootRef.current;
    if (!root) return [];
    return Array.from(
      root.querySelectorAll<HTMLElement>(`[${FILE_TREE_ROW_ATTRIBUTE}]`),
    );
  }, []);

  useImperativeHandle(
    ref,
    () => ({
      getRowElement: (id) => {
        const root = rootRef.current;
        if (!root) return null;
        return root.querySelector<HTMLElement>(
          `[${FILE_TREE_ROW_ATTRIBUTE}="${CSS.escape(id)}"]`,
        );
      },
      scrollToRow: (id, options) => {
        const root = rootRef.current;
        if (!root) return false;
        const element = root.querySelector<HTMLElement>(
          `[${FILE_TREE_ROW_ATTRIBUTE}="${CSS.escape(id)}"]`,
        );
        if (!element) return false;
        element.scrollIntoView({ block: 'nearest', ...options });
        return true;
      },
    }),
    [],
  );

  const scheduleTooltip = useCallback((rowId: string) => {
    if (tooltipTimerRef.current !== undefined) {
      window.clearTimeout(tooltipTimerRef.current);
    }
    tooltipTimerRef.current = window.setTimeout(() => {
      setTooltipRowId(rowId);
      tooltipTimerRef.current = undefined;
    }, 400);
  }, []);

  const cancelTooltip = useCallback(() => {
    if (tooltipTimerRef.current !== undefined) {
      window.clearTimeout(tooltipTimerRef.current);
      tooltipTimerRef.current = undefined;
    }
    setTooltipRowId(null);
  }, []);

  const moveFocus = useCallback(
    (current: HTMLElement, delta: number) => {
      const elements = queryRowElements();
      const index = elements.indexOf(current);
      if (index === -1) return;
      const next = elements[index + delta];
      next?.focus();
    },
    [queryRowElements],
  );

  const handleRowKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>, row: FileTreeEntryRow) => {
      switch (event.key) {
        case 'ArrowDown':
          event.preventDefault();
          moveFocus(event.currentTarget, 1);
          break;
        case 'ArrowUp':
          event.preventDefault();
          moveFocus(event.currentTarget, -1);
          break;
        case 'ArrowRight':
          if (row.type === 'directory' && !row.expanded) {
            event.preventDefault();
            onToggleExpand?.(row);
          }
          break;
        case 'ArrowLeft':
          if (row.type === 'directory' && row.expanded) {
            event.preventDefault();
            onToggleExpand?.(row);
          }
          break;
        case 'Enter':
        case ' ':
          event.preventDefault();
          if (!row.disabled) onSelect?.(row);
          break;
        default:
          break;
      }
    },
    [moveFocus, onSelect, onToggleExpand],
  );

  const renderEntryRow = (row: FileTreeEntryRow): ReactNode => {
    const isDirectory = row.type === 'directory';
    const canExpand = isDirectory && row.hasChildren !== false;
    const isSelected = selectedId === row.id;
    const attributes = getRowAttributes?.(row) ?? {};
    const {
      className: extraClassName,
      style: extraStyle,
      ...extraAttributes
    } = attributes;
    const dashboard = renderDashboard?.(row) ?? null;
    const tooltip = renderRowTooltip?.(row) ?? null;

    const handleRowClick = (event: MouseEvent<HTMLDivElement>) => {
      // 点击展开按钮时不再冒泡触发选中。
      if ((event.target as HTMLElement).closest('[data-role="toggle"]')) return;
      if (row.disabled) return;
      onSelect?.(row);
    };

    return (
      <div
        key={row.id}
        role="treeitem"
        tabIndex={0}
        aria-level={row.depth + 1}
        aria-selected={isSelected}
        aria-expanded={canExpand ? Boolean(row.expanded) : undefined}
        aria-disabled={row.disabled || undefined}
        {...extraAttributes}
        {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
        className={[styles.row, extraClassName].filter(Boolean).join(' ')}
        data-selected={isSelected || undefined}
        data-disabled={row.disabled || undefined}
        data-kind={row.kind}
        style={{ paddingLeft: `calc(${row.depth} * 16px)`, ...extraStyle }}
        onClick={handleRowClick}
        onKeyDown={(event) => handleRowKeyDown(event, row)}
        onMouseEnter={() => scheduleTooltip(row.id)}
        onMouseLeave={cancelTooltip}
        onFocus={() => scheduleTooltip(row.id)}
        onBlur={cancelTooltip}
      >
        {canExpand ? (
          <button
            type="button"
            data-role="toggle"
            className={styles.toggle}
            aria-label={fillName(
              row.expanded ? text.collapse : text.expand,
              row.name,
            )}
            tabIndex={-1}
            onClick={(event) => {
              event.stopPropagation();
              onToggleExpand?.(row);
            }}
          >
            <Chevron expanded={Boolean(row.expanded)} />
          </button>
        ) : (
          <span aria-hidden="true" className={styles.toggleSpacer} />
        )}

        <span className={styles.iconSlot}>
          {isDirectory ? (
            <FolderIcon open={Boolean(row.expanded)} />
          ) : (
            <FileIcon />
          )}
        </span>

        <span className={styles.labelGroup}>
          <span className={styles.label} title={row.name}>
            {row.name}
          </span>
          {row.description ? (
            <span className={styles.description}>{row.description}</span>
          ) : null}
        </span>

        {row.busy ? <Spinner /> : null}

        {row.expiresAt ? (
          <span className={styles.expiry} title={row.expiresAt}>
            {onOpenRetentionSettings ? (
              <button
                type="button"
                className={styles.expiryAction}
                onClick={(event) => {
                  event.stopPropagation();
                  onOpenRetentionSettings(row.uri);
                }}
              >
                {row.expiresAt}
              </button>
            ) : (
              row.expiresAt
            )}
          </span>
        ) : null}

        {row.levelLabel ? (
          <span className={styles.level}>{row.levelLabel}</span>
        ) : null}

        {dashboard ? (
          <span className={styles.dashboard}>{dashboard}</span>
        ) : null}

        {tooltip && tooltipRowId === row.id ? (
          <span role="tooltip" className={styles.tooltip}>
            {tooltip}
          </span>
        ) : null}
      </div>
    );
  };

  const renderChildRow = (row: FileTreeRow): ReactNode => {
    switch (row.kind) {
      case 'loading':
        return (
          <div
            key={row.id}
            role="treeitem"
            aria-level={row.depth + 1}
            aria-busy="true"
            {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
            className={styles.childRow}
            style={{ paddingLeft: `calc(${row.depth} * 16px)` }}
          >
            <span className={styles.toggleSpacer} />
            <Spinner />
            <span className={styles.muted}>{text.loading}</span>
          </div>
        );
      case 'load-more':
        return (
          <div
            key={row.id}
            role="treeitem"
            aria-level={row.depth + 1}
            {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
            className={styles.childRow}
            style={{ paddingLeft: `calc(${row.depth} * 16px)` }}
          >
            <button
              type="button"
              className={styles.childAction}
              disabled={row.loading}
              onClick={() => onLoadMore?.(row as FileTreeLoadMoreRow)}
            >
              {row.loading ? <Spinner /> : null}
              {text.loadMore}
            </button>
          </div>
        );
      case 'error':
        return (
          <div
            key={row.id}
            role="treeitem"
            aria-level={row.depth + 1}
            {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
            className={styles.childRow}
            data-tone="error"
            style={{ paddingLeft: `calc(${row.depth} * 16px)` }}
          >
            <span className={styles.muted}>{row.message ?? text.error}</span>
            <button
              type="button"
              className={styles.childAction}
              onClick={() => onRetry?.(row as FileTreeErrorRow)}
            >
              {text.retry}
            </button>
          </div>
        );
      case 'empty':
        return (
          <div
            key={row.id}
            role="treeitem"
            aria-level={row.depth + 1}
            {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
            className={styles.childRow}
            style={{ paddingLeft: `calc(${row.depth} * 16px)` }}
          >
            <span className={styles.muted}>{text.empty}</span>
          </div>
        );
      default:
        return renderEntryRow(row);
    }
  };

  let body: ReactNode;
  if (!isReady) {
    body = (
      <div className={styles.state}>
        {loadingFallback ?? (
          <span className={styles.muted}>{text.loading}</span>
        )}
      </div>
    );
  } else if (isWholeTreeError(rows)) {
    body = (
      <div className={styles.state}>
        {errorFallback ?? (
          <span className={styles.muted}>
            {(rows[0] as FileTreeErrorRow).message ?? text.error}
          </span>
        )}
      </div>
    );
  } else if (rows.length === 0) {
    body = (
      <div className={styles.state}>
        {emptyFallback ?? <span className={styles.muted}>{text.empty}</span>}
      </div>
    );
  } else {
    body = (
      <div role="group" className={styles.rows}>
        {rows.map(renderChildRow)}
      </div>
    );
  }

  return (
    <div
      {...rest}
      ref={rootRef}
      className={[styles.root, className].filter(Boolean).join(' ')}
    >
      {slots?.toolbar ? (
        <div className={styles.toolbar}>{slots.toolbar}</div>
      ) : null}
      <div role="tree" aria-label={rest['aria-label']} className={styles.body}>
        {body}
      </div>
    </div>
  );
});
