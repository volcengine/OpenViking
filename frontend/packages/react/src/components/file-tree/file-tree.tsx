import { forwardRef, useImperativeHandle, useRef } from 'react';
import type { KeyboardEvent } from 'react';

import { FileTreeBody } from './file-tree-body.js';
import { findRowElement } from './file-tree-dom.js';
import { handleRowKeyDown } from './file-tree-keyboard.js';
import { DEFAULT_LABELS } from './file-tree-labels.js';
import type { FileTreeRowContext } from './file-tree-row-context.js';
import { useRowTooltip } from './file-tree-tooltip.js';
import { useFileTreeActions } from './use-file-tree-actions.js';
import type {
  FileTreeComponent,
  FileTreeEntryRow,
  FileTreeHandle,
  FileTreeProps,
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
  FileTreeOperations,
  FileTreeProps,
  FileTreeRow,
  FileTreeRowBase,
  FileTreeSlots,
} from './types.js';

export const FileTree: FileTreeComponent = forwardRef<
  FileTreeHandle,
  FileTreeProps
>(function FileTree(
  {
    rows,
    operations,
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
  const text = { ...DEFAULT_LABELS, ...labels };
  const tooltip = useRowTooltip();
  const actions = useFileTreeActions({
    operations,
    onSelect,
    onToggleExpand,
    onLoadMore,
    onRetry,
    onOpenRetentionSettings,
  });

  useImperativeHandle(
    ref,
    () => ({
      getRowElement: (id) => findRowElement(rootRef.current, id),
      scrollToRow: (id, options) => {
        const element = findRowElement(rootRef.current, id);
        if (!element) return false;
        element.scrollIntoView({ block: 'nearest', ...options });
        return true;
      },
    }),
    [],
  );

  const rowContext: FileTreeRowContext = {
    text,
    selectedId,
    tooltipRowId: tooltip.tooltipRowId,
    getRowAttributes,
    renderDashboard,
    renderRowTooltip,
    ...actions,
    onRowKeyDown: (
      event: KeyboardEvent<HTMLDivElement>,
      row: FileTreeEntryRow,
    ) =>
      handleRowKeyDown(event, {
        root: rootRef.current,
        row,
        onSelect: actions.onSelect,
        onToggleExpand: actions.onToggleExpand,
      }),
    onRowEnter: tooltip.show,
    onRowLeave: tooltip.hide,
  };

  return (
    <div
      {...rest}
      ref={rootRef}
      className={['ov-file-tree', className].filter(Boolean).join(' ')}
    >
      {slots?.toolbar ? (
        <div className="ov-file-tree__toolbar">{slots.toolbar}</div>
      ) : null}
      <div
        role="tree"
        aria-label={rest['aria-label']}
        className="ov-file-tree__body"
      >
        <FileTreeBody
          rows={rows}
          isReady={isReady}
          text={text}
          loadingFallback={loadingFallback}
          errorFallback={errorFallback}
          emptyFallback={emptyFallback}
          ctx={rowContext}
        />
      </div>
    </div>
  );
});
