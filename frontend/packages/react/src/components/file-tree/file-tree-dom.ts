import { FILE_TREE_ROW_ATTRIBUTE } from './types.js';

/** 按 DOM 顺序取出所有行元素，用于键盘上下移动焦点。 */
export function queryRowElements(root: HTMLElement | null) {
  if (!root) return [];
  return Array.from(
    root.querySelectorAll<HTMLElement>(`[${FILE_TREE_ROW_ATTRIBUTE}]`),
  );
}

/** 按行 id 找到行元素；行不在当前可见列表中时返回 `null`。 */
export function findRowElement(root: HTMLElement | null, id: string) {
  if (!root) return null;
  return root.querySelector<HTMLElement>(
    `[${FILE_TREE_ROW_ATTRIBUTE}="${CSS.escape(id)}"]`,
  );
}
