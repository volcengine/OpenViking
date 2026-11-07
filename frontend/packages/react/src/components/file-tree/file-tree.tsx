import { forwardRef, useImperativeHandle, useRef } from 'react';

import { FILE_TREE_ROW_ATTRIBUTE } from './types.js';
import type { FileTreeHandle, FileTreeProps } from './types.js';

export { FileTreeSpinner } from './file-tree-spinner.js';
export {
  resolveFileTreeInfoKey,
  showsInlineDescription,
  splitVikingUri,
} from './file-tree-info.js';
export {
  DEFAULT_INFO_LABELS,
  resolveInfoCopy,
} from './file-tree-info-labels.js';
export { FILE_TREE_ROW_ATTRIBUTE } from './types.js';
export type {
  FileTreeChildRow,
  FileTreeComponent,
  FileTreeEmptyRow,
  FileTreeEntryRow,
  FileTreeErrorRow,
  FileTreeHandle,
  FileTreeInfoKey,
  FileTreeInfoLabels,
  FileTreeLabels,
  FileTreeLoadingRow,
  FileTreeLoadMoreRow,
  FileTreeOperations,
  FileTreeProps,
  FileTreeRow,
  FileTreeRowBase,
  FileTreeSlots,
} from './types.js';

/**
 * 占位组件：只渲染固定尺寸的空壳，真实视图按 types.ts 的契约重写。
 * 保留 `FileTreeProps` 与 `forwardRef` 契约，消费者可以照文档里的用法写
 * JSX 并拿到句柄；还没有行可查，`getRowElement` 返回 `null`、
 * `scrollToRow` 返回 `false`。
 *
 * Placeholder: renders a fixed shell until the real view lands against the
 * contract in types.ts. It keeps the `FileTreeProps` and `forwardRef` contracts
 * so consumers can write the JSX the docs show and receive a handle; with no
 * rows yet, `getRowElement` returns `null` and `scrollToRow` returns `false`.
 */
export const FileTree = forwardRef<FileTreeHandle, FileTreeProps>(
  function FileTree(
    {
      rows: _rows,
      operations: _operations,
      selectedId: _selectedId,
      isReady: _isReady,
      loadingFallback: _loading,
      errorFallback: _error,
      emptyFallback: _empty,
      slots: _slots,
      labels: _labels,
      getRowAttributes: _getRowAttributes,
      renderDashboard: _renderDashboard,
      renderRowTooltip: _renderRowTooltip,
      onSelect: _onSelect,
      onToggleExpand: _onToggleExpand,
      onLoadMore: _onLoadMore,
      onRetry: _onRetry,
      onOpenRetentionSettings: _onRetention,
      className,
      ...rest
    },
    ref,
  ) {
    const rootRef = useRef<HTMLDivElement>(null);

    useImperativeHandle(
      ref,
      () => ({
        getRowElement: (id) =>
          rootRef.current?.querySelector<HTMLElement>(
            `[${FILE_TREE_ROW_ATTRIBUTE}="${CSS.escape(id)}"]`,
          ) ?? null,
        scrollToRow: (id, options) => {
          const element = rootRef.current?.querySelector<HTMLElement>(
            `[${FILE_TREE_ROW_ATTRIBUTE}="${CSS.escape(id)}"]`,
          );
          if (!element) return false;
          element.scrollIntoView({ block: 'nearest', ...options });
          return true;
        },
      }),
      [],
    );

    return (
      <div
        {...rest}
        ref={rootRef}
        className={['ov-file-tree', className].filter(Boolean).join(' ')}
      >
        FileTree
      </div>
    );
  },
);
