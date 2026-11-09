import type { ReactNode } from 'react';

import {
  FileTreeInfoTooltip,
  resolveFileTreeInfoCopy,
} from './file-tree-info-tooltip.js';
import type { FileTreeEntryRow, FileTreeInfoLabels } from './types.js';

/**
 * 行浮层的外壳。宿主提供 `renderRowTooltip` 时只渲染它返回的内容；它返回 `null`
 * 表示这一行不显示浮层，不回退到内置内容。
 *
 * 宿主没有提供时，用按 `uri` 解析的内置浮层。先解析内容、再决定是否创建外壳，
 * 因此没有可显示内容时不会留下一个只有背景和内边距的空壳。
 *
 * The shell around a row tooltip. When the host supplies `renderRowTooltip`, only
 * its result renders; returning `null` hides the tooltip for that row and does
 * not fall back to the built-in content.
 *
 * When the host supplies none, the built-in tooltip resolved from the row `uri`
 * renders. The content is resolved before the shell is created, so with nothing
 * to show no empty shell with just background and padding is left behind.
 */
export function FileTreeTooltipSurface({
  row,
  infoLabels,
  renderRowTooltip,
}: {
  row: FileTreeEntryRow;
  infoLabels?: FileTreeInfoLabels;
  renderRowTooltip?: (row: FileTreeEntryRow) => ReactNode;
}) {
  if (renderRowTooltip) {
    const custom = renderRowTooltip(row);
    if (custom === null || custom === undefined || custom === false)
      return null;

    return (
      <span role="tooltip" className="ov-file-tree__tooltip">
        {custom}
      </span>
    );
  }

  const copy = resolveFileTreeInfoCopy(row, infoLabels);
  if (!copy) return null;

  return (
    <span role="tooltip" className="ov-file-tree__tooltip">
      <FileTreeInfoTooltip copy={copy} />
    </span>
  );
}
