import type { PathSegment } from './file-preview-path.js';

export interface FilePreviewBreadcrumbMenuProps {
  segments: readonly PathSegment[];
  canOpen: boolean;
  disabled: boolean;
  onOpen: (uri: string) => void;
}

/**
 * 折叠层级的菜单。用 `details` 提供键盘与点击，同时由 CSS 在悬停时展开——
 * 鼠标划过「…」就能看到被收起的层级，不必先点一下。
 *
 * The menu of collapsed levels. `details` supplies keyboard and click behaviour,
 * while CSS opens it on hover so passing over "…" reveals the hidden levels
 * without a click first.
 */
export function FilePreviewBreadcrumbMenu({
  segments,
  canOpen,
  disabled,
  onOpen,
}: FilePreviewBreadcrumbMenuProps) {
  return (
    <details className="ov-file-preview__breadcrumb-more">
      <summary
        aria-label="Show hidden path levels"
        aria-haspopup="menu"
        className="ov-file-preview__breadcrumb-more-summary"
      >
        …
      </summary>
      <ul className="ov-file-preview__breadcrumb-menu" role="menu">
        {segments.map((segment) => (
          <li key={segment.uri} role="none">
            <button
              type="button"
              role="menuitem"
              disabled={!canOpen || disabled}
              title={segment.uri}
              onClick={() => onOpen(segment.uri)}
            >
              {segment.label}/
            </button>
          </li>
        ))}
      </ul>
    </details>
  );
}
