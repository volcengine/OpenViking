import type { MouseEvent } from 'react';

import { Chevron, FileIcon, FolderIcon, Spinner } from './file-tree-icons.js';
import { fillName } from './file-tree-labels.js';
import { rowIndent, type FileTreeRowContext } from './file-tree-row-context.js';
import { FILE_TREE_ROW_ATTRIBUTE, type FileTreeEntryRow } from './types.js';

export function FileTreeEntryRowView({
  row,
  ctx,
}: {
  row: FileTreeEntryRow;
  ctx: FileTreeRowContext;
}) {
  const {
    text,
    selectedId,
    tooltipRowId,
    getRowAttributes,
    renderDashboard,
    renderRowTooltip,
    onSelect,
    onToggleExpand,
    onOpenRetentionSettings,
    onRowKeyDown,
    onRowEnter,
    onRowLeave,
  } = ctx;

  const isDirectory = row.type === 'directory';
  const canExpand = isDirectory && row.hasChildren !== false;
  const isSelected = selectedId === row.id;
  const {
    className: extraClassName,
    style: extraStyle,
    ...extraAttributes
  } = getRowAttributes?.(row) ?? {};
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
      role="treeitem"
      tabIndex={0}
      aria-level={row.depth + 1}
      aria-selected={isSelected}
      aria-expanded={canExpand ? Boolean(row.expanded) : undefined}
      aria-disabled={row.disabled || undefined}
      {...extraAttributes}
      {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
      className={['ov-file-tree__row', extraClassName]
        .filter(Boolean)
        .join(' ')}
      data-selected={isSelected || undefined}
      data-disabled={row.disabled || undefined}
      data-kind={row.kind}
      style={{ ...rowIndent(row), ...extraStyle }}
      onClick={handleRowClick}
      onKeyDown={(event) => onRowKeyDown(event, row)}
      onMouseEnter={() => onRowEnter(row.id)}
      onMouseLeave={onRowLeave}
      onFocus={() => onRowEnter(row.id)}
      onBlur={onRowLeave}
    >
      {canExpand ? (
        <button
          type="button"
          data-role="toggle"
          className="ov-file-tree__toggle"
          aria-label={fillName(
            row.expanded ? text.collapse : text.expand,
            row.name,
          )}
          tabIndex={-1}
          disabled={!onToggleExpand || row.disabled || row.busy}
          onClick={(event) => {
            event.stopPropagation();
            onToggleExpand?.(row);
          }}
        >
          <Chevron expanded={Boolean(row.expanded)} />
        </button>
      ) : (
        <span aria-hidden="true" className="ov-file-tree__toggle-spacer" />
      )}

      <span className="ov-file-tree__icon-slot">
        {isDirectory ? (
          <FolderIcon open={Boolean(row.expanded)} />
        ) : (
          <FileIcon />
        )}
      </span>

      <span className="ov-file-tree__label-group">
        <span className="ov-file-tree__label" title={row.name}>
          {row.name}
        </span>
        {row.description ? (
          <span className="ov-file-tree__description">{row.description}</span>
        ) : null}
      </span>

      {row.busy ? <Spinner /> : null}

      {row.expiresAt ? (
        <span className="ov-file-tree__expiry" title={row.expiresAt}>
          {onOpenRetentionSettings ? (
            <button
              type="button"
              className="ov-file-tree__expiry-action"
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
        <span className="ov-file-tree__level">{row.levelLabel}</span>
      ) : null}

      {dashboard ? (
        <span className="ov-file-tree__dashboard">{dashboard}</span>
      ) : null}

      {tooltip && tooltipRowId === row.id ? (
        <span role="tooltip" className="ov-file-tree__tooltip">
          {tooltip}
        </span>
      ) : null}
    </div>
  );
}
