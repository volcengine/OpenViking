import { forwardRef, useImperativeHandle, useRef } from 'react';

import { resolveFileTreeLabels } from './file-tree-labels.js';
import { FileTreeBody } from './file-tree-body.js';
import { handleRowKeyDown } from './file-tree-keyboard.js';
import type { FileTreeRowContext } from './file-tree-row-context.js';
import { FileTreeToolbar } from './file-tree-toolbar.js';
import type {
  FileTreeComponent,
  FileTreeHandle,
  FileTreeProps,
} from './types.js';
import { FILE_TREE_ROW_ATTRIBUTE } from './types.js';
import { useFileTreeActions } from './use-file-tree-actions.js';

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

/** 按行 id 找到行元素。Finds a row element by id. */
function findRow(root: HTMLElement | null, id: string) {
  return (
    root?.querySelector<HTMLElement>(
      `[${FILE_TREE_ROW_ATTRIBUTE}="${CSS.escape(id)}"]`,
    ) ?? null
  );
}

/**
 * 目录树。宿主把树状态展平成有序行列表传进来，组件负责渲染、键盘交互，
 * 并在操作成功后通知。
 *
 * A file tree. The host flattens its tree state into an ordered row list; the
 * component renders it, handles keyboard interaction and reports successful
 * operations.
 */
export const FileTree: FileTreeComponent = forwardRef<
  FileTreeHandle,
  FileTreeProps
>(function FileTree(
  {
    rows,
    locale,
    operations,
    selectedId,
    search,
    refresh,
    isReady = true,
    loadingFallback,
    errorFallback,
    emptyFallback,
    slots,
    labels,
    getRowAttributes,
    renderDashboard,
    renderRowTooltip,
    renderRowIcon,
    onSelect,
    onToggleExpand,
    onLoadMore,
    onRetry,
    onOpenRetentionSettings,
    onSearch,
    onRefresh,
    className,
    ...rest
  },
  ref,
) {
  const rootRef = useRef<HTMLDivElement>(null);
  const text = resolveFileTreeLabels(locale, labels);
  const actions = useFileTreeActions({
    operations,
    onSelect,
    onToggleExpand,
    onLoadMore,
    onRetry,
    onOpenRetentionSettings,
    onSearch,
    onRefresh,
  });

  useImperativeHandle(
    ref,
    () => ({
      getRowElement: (id) => findRow(rootRef.current, id),
      scrollToRow: (id, options) => {
        const element = findRow(rootRef.current, id);
        if (!element) return false;
        element.scrollIntoView({ block: 'nearest', ...options });
        return true;
      },
    }),
    [],
  );

  const ctx: FileTreeRowContext = {
    text,
    infoLabels: text.info,
    selectedId,
    getRowAttributes,
    renderDashboard,
    renderRowTooltip,
    renderRowIcon,
    renderSpinner: slots?.spinner ? () => slots.spinner : undefined,
    ...actions,
    onRowKeyDown: (event, row) =>
      handleRowKeyDown(event, {
        root: rootRef.current,
        row,
        onSelect: actions.onSelect,
        onToggleExpand: actions.onToggleExpand,
      }),
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
      <FileTreeToolbar
        search={search}
        refresh={refresh}
        text={text}
        refreshing={actions.refreshing}
        onSearch={actions.onSearch}
        onRefresh={actions.onRefresh}
      />
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
          ctx={ctx}
        />
      </div>
    </div>
  );
});
