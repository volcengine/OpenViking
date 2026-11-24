import type { ReactNode } from 'react';

import {
  FileTreeInfoTooltip,
  resolveFileTreeInfoCopy,
} from './file-tree-info-tooltip.js';
import type { FileTreeEntryRow, FileTreeInfoLabels } from './types.js';

/**
 * 行浮层的内容。宿主提供 `renderRowTooltip` 时只渲染它返回的内容；它返回 `null`
 * 表示这一行不显示浮层，不回退到内置内容。
 *
 * 宿主没有提供时，用按 `uri` 解析的内置内容。先解析内容、再决定是否渲染，
 * 因此没有可显示内容时不会留下一个只有背景和内边距的空壳。
 *
 * The content of a row surface. When the host supplies `renderRowTooltip`, only
 * its result renders; returning `null` hides the surface for that row and does
 * not fall back to the built-in content.
 *
 * When the host supplies none, the built-in content resolved from the row `uri`
 * renders. The content is resolved before anything is rendered, so with nothing
 * to show no empty shell with just background and padding is left behind.
 */
export function FileTreeTooltipContent({
  row,
  infoLabels,
  renderRowTooltip,
}: {
  row: FileTreeEntryRow;
  infoLabels?: FileTreeInfoLabels;
  renderRowTooltip?: (row: FileTreeEntryRow) => ReactNode;
}): ReactNode {
  if (renderRowTooltip) {
    const custom = renderRowTooltip(row);
    if (custom === null || custom === undefined || custom === false) {
      return null;
    }
    return custom;
  }

  const copy = resolveFileTreeInfoCopy(row, infoLabels);
  if (!copy) return null;

  return <FileTreeInfoTooltip copy={copy} />;
}
