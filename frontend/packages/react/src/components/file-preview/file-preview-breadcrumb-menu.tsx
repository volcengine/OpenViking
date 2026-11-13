import { Tooltip } from '../../shared/primitives/tooltip/index.js';
import type { PathSegment } from './file-preview-path.js';

export interface FilePreviewBreadcrumbMenuProps {
  hiddenPathLabel: string;
  segments: readonly PathSegment[];
  canOpen: boolean;
  disabled: boolean;
  onOpen: (uri: string) => void;
}

/**
 * 折叠层级的菜单。用 Tooltip 的 `click` 形态承载：点击「…」展开，按 Esc 或
 * 点外部关闭，浮层渲染到 body，因此不受面包屑容器的布局约束。
 *
 * The menu of collapsed levels, carried by Tooltip's `click` shape: clicking "…"
 * opens it, Escape or a press outside closes it, and because the surface renders
 * into `body` the breadcrumb's own layout cannot clip it.
 */
export function FilePreviewBreadcrumbMenu({
  hiddenPathLabel,
  segments,
  canOpen,
  disabled,
  onOpen,
}: FilePreviewBreadcrumbMenuProps) {
  return (
    <Tooltip
      trigger="click"
      placement="bottom"
      className="ov-file-preview__breadcrumb-menu-surface"
      content={
        <ul className="ov-file-preview__breadcrumb-menu" role="menu">
          {segments.map((segment) => (
            <li key={segment.uri} role="none">
              <button
                type="button"
                role="menuitem"
                disabled={!canOpen || disabled}
                onClick={() => onOpen(segment.uri)}
              >
                {segment.label}/
              </button>
            </li>
          ))}
        </ul>
      }
    >
      <button
        type="button"
        className="ov-file-preview__breadcrumb-more"
        aria-label={hiddenPathLabel}
        aria-haspopup="menu"
      >
        …
      </button>
    </Tooltip>
  );
}
