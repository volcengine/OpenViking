import {
  ChevronRightIcon,
  DocumentIcon,
  FolderIcon,
  FolderOpenIcon,
} from '../../icons/index.js';

/**
 * 文件夹图标。展开态与收起态叠放在一起交叉淡入，和宿主一致。
 *
 * The folder icon. The open and collapsed states sit stacked and cross-fade,
 * matching the host tree.
 */
export function FileTreeFolderIcon({ open }: { open: boolean }) {
  return (
    <span className="ov-file-tree__folder">
      <FolderIcon className="ov-file-tree__folder-layer" data-open={!open} />
      <FolderOpenIcon className="ov-file-tree__folder-layer" data-open={open} />
    </span>
  );
}

/** 宿主没有提供 `renderRowIcon` 时的文件图标。Fallback file icon. */
export function FileTreeFileIcon() {
  return <DocumentIcon className="ov-file-tree__file-icon" />;
}

/** 展开箭头，展开时旋转 90°。The expand chevron, rotated when open. */
export function FileTreeChevron({ open }: { open: boolean }) {
  return (
    <ChevronRightIcon
      className="ov-file-tree__chevron"
      data-expanded={open || undefined}
      width={12}
      height={12}
    />
  );
}
