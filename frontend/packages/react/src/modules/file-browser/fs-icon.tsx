import type { CSSProperties, ReactNode } from 'react';

import {
  FolderIcon,
  getFileTypeColor,
  getFileTypeIcon,
  resolveFileTypeIconKey,
} from '../../icons/index.js';

/**
 * 文件行的类型图标。同一份图标集里每个键都登记了浅色与暗色两套色值，交给
 * `color` 由 CSS 跟随宿主的 `.dark`，不在这里判断主题。
 *
 * 树行与预览页签共用它，两处的图标因此完全一致——页签不该另画一套。
 *
 * The type icon of a file row. Every key in the icon set registers a light and a
 * dark colour; `color` lets CSS follow the host's `.dark` rather than deciding the
 * scheme here.
 *
 * The tree row and the preview tab share it, so both show the same icon — a tab
 * has no business drawing its own.
 */
export function fileTypeIcon(name: string): ReactNode {
  const Icon = getFileTypeIcon(name);
  const key = resolveFileTypeIconKey(name);
  return (
    <Icon
      className="ov-file-tree__file-type-icon"
      style={
        {
          '--ov-file-tree-icon-light': getFileTypeColor(key, 'light'),
          '--ov-file-tree-icon-dark': getFileTypeColor(key, 'dark'),
        } as CSSProperties
      }
    />
  );
}

/** 目录的图标，与树上的文件夹图标同源。The directory icon, the tree's folder icon. */
export function directoryIcon(): ReactNode {
  return <FolderIcon className="ov-file-tree__file-type-icon" />;
}
