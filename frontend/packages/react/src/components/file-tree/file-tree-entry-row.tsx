import type { KeyboardEvent, MouseEvent } from 'react';

import {
  FileTreeChevron,
  FileTreeFileIcon,
  FileTreeFolderIcon,
} from './file-tree-icons.js';
import {
  resolveFileTreeInfoKey,
  showsInlineDescription,
} from './file-tree-info.js';
import { resolveInfoCopy } from './file-tree-info-labels.js';
import { Popover } from '../../shared/components/popover/index.js';
import { resolveFileTreeInfoCopy } from './file-tree-info-tooltip.js';
import { FileTreeTooltipContent } from './file-tree-tooltip-surface.js';
import type { FileTreeRowContext } from './file-tree-row-context.js';
import { Guides, rowStyle } from './file-tree-row-shared.js';
import type { FileTreeEntryRow } from './types.js';
import { FILE_TREE_ROW_ATTRIBUTE } from './types.js';

/**
 * 一条条目行：展开箭头、图标、名称、行内说明、行尾指示器与悬停浮层。
 *
 * One entry row: chevron, icon, name, inline description, the row-end indicator
 * and the hover tooltip.
 */
export function FileTreeRowView({
  row,
  ctx,
}: {
  row: FileTreeEntryRow;
  ctx: FileTreeRowContext;
}) {
  const {
    text,
    selectedId,
    getRowAttributes,
    renderDashboard,
    renderRowTooltip,
    renderRowIcon,
    onSelect,
    onToggleExpand,
    onOpenRetentionSettings,
    onRowKeyDown,
  } = ctx;

  const isDirectory = row.type === 'directory';
  const canExpand = isDirectory && row.hasChildren !== false;
  const isExpanded = Boolean(row.expanded);
  const isSelected = selectedId === row.id;
  const extra = getRowAttributes?.(row) ?? {};
  const {
    className: extraClassName,
    style: extraStyle,
    ...extraAttributes
  } = extra;
  const dashboard = renderDashboard?.(row) ?? null;
  // 宿主给了 description 就用它（含显式空串）；否则按 uri 取内置说明。
  const infoKey = resolveFileTreeInfoKey(row.uri);
  const description =
    row.description ??
    (infoKey && showsInlineDescription(infoKey)
      ? resolveInfoCopy(infoKey, ctx.infoLabels).description
      : undefined);

  const handleClick = (event: MouseEvent<HTMLDivElement>) => {
    // 点展开箭头时不再冒泡触发选中。The chevron handles its own click.
    if ((event.target as HTMLElement).closest('[data-role="toggle"]')) return;
    if (row.disabled) return;
    onSelect?.(row);
  };

  const handleToggle = (event: MouseEvent<HTMLButtonElement>) => {
    event.stopPropagation();
    onToggleExpand?.(row);
  };

  const rowNode = (
    <div
      role="treeitem"
      tabIndex={0}
      aria-level={row.depth + 1}
      aria-selected={isSelected}
      aria-expanded={canExpand ? isExpanded : undefined}
      aria-disabled={row.disabled || undefined}
      {...extraAttributes}
      {...{ [FILE_TREE_ROW_ATTRIBUTE]: row.id }}
      className={['ov-file-tree__row', extraClassName]
        .filter(Boolean)
        .join(' ')}
      data-selected={isSelected || undefined}
      data-disabled={row.disabled || undefined}
      data-kind={row.kind}
      style={{ ...rowStyle(row.depth), ...extraStyle }}
      onClick={handleClick}
      onKeyDown={(event: KeyboardEvent<HTMLDivElement>) =>
        onRowKeyDown(event, row)
      }
    >
      {/* 每一级祖先画一条竖线，上下各溢出 1px 让相邻行接上。 */}
      <Guides depth={row.depth} />

      {canExpand ? (
        <button
          type="button"
          data-role="toggle"
          className="ov-file-tree__toggle"
          aria-label={
            isExpanded
              ? text.collapse.replace('{name}', row.name)
              : text.expand.replace('{name}', row.name)
          }
          tabIndex={-1}
          disabled={!onToggleExpand || row.disabled}
          onClick={handleToggle}
        >
          {row.busy ? (
            (ctx.renderSpinner?.() ?? (
              <span className="ov-file-tree__spinner" />
            ))
          ) : (
            <FileTreeChevron open={isExpanded} />
          )}
        </button>
      ) : (
        <span aria-hidden="true" className="ov-file-tree__toggle-spacer" />
      )}

      <span className="ov-file-tree__icon-slot">
        {isDirectory ? (
          <FileTreeFolderIcon open={isExpanded} />
        ) : (
          (renderRowIcon?.(row) ?? <FileTreeFileIcon />)
        )}
      </span>

      <span
        className="ov-file-tree__label-group"
        data-description={Boolean(description) || undefined}
      >
        <span className="ov-file-tree__label" title={row.name}>
          {row.name}
        </span>
        {description ? (
          <span className="ov-file-tree__description">{description}</span>
        ) : null}
      </span>

      {row.busy && !canExpand ? (
        <span className="ov-file-tree__spinner" />
      ) : null}

      {row.expiresAt ? (
        <span className="ov-file-tree__expiry">
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
    </div>
  );

  // 有没有内容要先算出来，不能靠渲染后的元素判断——JSX 元素永远非空，那样
  // 守卫会失效，每行都会挂一个空浮层。
  //
  // Whether there is content is decided up front rather than from the rendered
  // element: a JSX element is never null, so that guard would never fire and every
  // row would carry an empty surface.
  const custom = renderRowTooltip?.(row);
  const hasContent = renderRowTooltip
    ? custom !== null && custom !== undefined && custom !== false
    : resolveFileTreeInfoCopy(row, ctx.infoLabels) !== null;
  if (!hasContent) return rowNode;

  const tooltipContent = (
    <FileTreeTooltipContent
      row={row}
      infoLabels={ctx.infoLabels}
      renderRowTooltip={renderRowTooltip}
    />
  );

  // 悬停延迟、翻面与 portal 都由 Popover 负责：它渲染到 body，因此不会被树的
  // `overflow` 裁掉；方位固定在右侧，与设计稿一致。
  return (
    <Popover content={tooltipContent} placement="right" trigger="hover">
      {rowNode}
    </Popover>
  );
}
