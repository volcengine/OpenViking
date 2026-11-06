import type { KeyboardEvent } from 'react';

import { queryRowElements } from './file-tree-dom.js';
import type { FileTreeEntryRow } from './types.js';

export interface FileTreeKeyHandlerOptions {
  root: HTMLElement | null;
  row: FileTreeEntryRow;
  onSelect?: (row: FileTreeEntryRow) => void;
  onToggleExpand?: (row: FileTreeEntryRow) => void;
}

/** 上下方向键在同一层的行元素间移动焦点。 */
function moveFocus(
  current: HTMLElement,
  root: HTMLElement | null,
  delta: number,
) {
  const elements = queryRowElements(root);
  const index = elements.indexOf(current);
  if (index === -1) return;
  elements[index + delta]?.focus();
}

/** 树行的键盘语义：上下移动焦点，左右展开折叠，回车与空格选中。 */
export function handleRowKeyDown(
  event: KeyboardEvent<HTMLDivElement>,
  { root, row, onSelect, onToggleExpand }: FileTreeKeyHandlerOptions,
) {
  switch (event.key) {
    case 'ArrowDown':
      event.preventDefault();
      moveFocus(event.currentTarget, root, 1);
      break;
    case 'ArrowUp':
      event.preventDefault();
      moveFocus(event.currentTarget, root, -1);
      break;
    case 'ArrowRight':
      if (
        !row.disabled &&
        !row.busy &&
        row.type === 'directory' &&
        !row.expanded
      ) {
        event.preventDefault();
        onToggleExpand?.(row);
      }
      break;
    case 'ArrowLeft':
      if (
        !row.disabled &&
        !row.busy &&
        row.type === 'directory' &&
        row.expanded
      ) {
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
}
