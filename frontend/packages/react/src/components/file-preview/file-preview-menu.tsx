import { Button } from '../../shared/components/button/button.js';
import { Dropdown } from './dropdown/index.js';
import MoreIcon from '../../../../icons/svg/file-preview/more.svg?react';
import type { FilePreviewAction } from './action-types.js';
import type { FilePreviewLabels } from './types.js';

/** The preview owns commands and pending state; Dropdown owns menu interaction. */
export function FilePreviewMenu({
  groups,
  disabled,
  pending,
  text,
  onSelect,
}: {
  groups: readonly (readonly FilePreviewAction[])[];
  disabled: boolean;
  pending: boolean;
  text: Required<FilePreviewLabels>;
  onSelect: (action: FilePreviewAction) => void;
}) {
  return (
    <Dropdown
      disabled={disabled}
      className="ov-file-preview__menu-surface"
      menuClassName="ov-file-preview__menu"
      groups={groups.map((group) =>
        group.map((action) => ({
          ...action,
          className: ['ov-file-preview__menu-item', action.className]
            .filter(Boolean)
            .join(' '),
          disabled: disabled || pending || action.disabled,
          onClick: () => onSelect(action),
        })),
      )}
    >
      <Button
        size="small"
        type="default"
        iconOnly
        className="ov-file-preview__toolbar-button"
        aria-label={text.moreActions}
        title={text.moreActions}
        disabled={disabled}
        aria-busy={pending}
      >
        <MoreIcon width={16} height={16} aria-hidden="true" />
      </Button>
    </Dropdown>
  );
}
