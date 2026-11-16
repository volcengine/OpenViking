import {
  Dropdown,
  DropdownItem,
} from '../../shared/primitives/dropdown/index.js';
import type { PathSegment } from './file-preview-path.js';

export interface FilePreviewBreadcrumbMenuProps {
  hiddenPathLabel: string;
  segments: readonly PathSegment[];
  canOpen: boolean;
  disabled: boolean;
  onOpen: (uri: string) => void;
}

/**
 * Collapsed parent navigation shares command-menu keyboard and focus behavior.
 */
export function FilePreviewBreadcrumbMenu({
  hiddenPathLabel,
  segments,
  canOpen,
  disabled,
  onOpen,
}: FilePreviewBreadcrumbMenuProps) {
  return (
    <Dropdown
      className="ov-file-preview__breadcrumb-menu-surface"
      menuClassName="ov-file-preview__breadcrumb-menu"
      alignment="center"
      trigger={
        <button
          type="button"
          className="ov-file-preview__breadcrumb-more"
          aria-label={hiddenPathLabel}
        >
          …
        </button>
      }
    >
      {segments.map((segment) => (
        <DropdownItem
          key={segment.uri}
          disabled={!canOpen || disabled}
          onClick={() => onOpen(segment.uri)}
        >
          {segment.label}/
        </DropdownItem>
      ))}
    </Dropdown>
  );
}
