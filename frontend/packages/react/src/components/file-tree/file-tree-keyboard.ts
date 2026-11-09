import type { KeyboardEvent } from 'react';

import { FILE_TREE_ROW_ATTRIBUTE } from './types.js';
import type { FileTreeEntryRow } from './types.js';

/** 按 DOM 顺序取出当前可见的行元素。Visible row elements, in DOM order. */
function visibleRows(root: HTMLElement | null) {
  if (!root) return [];
  return Array.from(
    root.querySelectorAll<HTMLElement>(`[${FILE_TREE_ROW_ATTRIBUTE}]`),
  );
}

/**
 * 树行的键盘语义：上下移动焦点，左右展开折叠，回车与空格选中。
 *
 * Keyboard semantics for a row: up/down move focus, left/right collapse and
 * expand, Enter and Space select.
 */
export function handleRowKeyDown(
  event: KeyboardEvent<HTMLDivElement>,
  {
    root,
    row,
    onSelect,
    onToggleExpand,
  }: {
    root: HTMLElement | null;
    row: FileTreeEntryRow;
    onSelect?: (row: FileTreeEntryRow) => void;
    onToggleExpand?: (row: FileTreeEntryRow) => void;
  },
) {
  const isDirectory = row.type === 'directory';
  const expanded = Boolean(row.expanded);
  const canToggle = isDirectory && !row.disabled;

  switch (event.key) {
    case 'ArrowDown':
    case 'ArrowUp': {
      event.preventDefault();
      const rows = visibleRows(root);
      const index = rows.indexOf(event.currentTarget);
      if (index === -1) return;
      rows[index + (event.key === 'ArrowDown' ? 1 : -1)]?.focus();
      return;
    }
    case 'ArrowRight':
      if (canToggle && !expanded) {
        event.preventDefault();
        onToggleExpand?.(row);
      }
      return;
    case 'ArrowLeft':
      if (canToggle && expanded) {
        event.preventDefault();
        onToggleExpand?.(row);
      }
      return;
    case 'Enter':
    case ' ':
      event.preventDefault();
      if (!row.disabled) onSelect?.(row);
      return;
    default:
      return;
  }
}
