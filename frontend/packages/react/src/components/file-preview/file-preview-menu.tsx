import { Button } from '../../shared/primitives/button/button.js';
import { Fragment } from 'react';
import {
  Dropdown,
  DropdownItem,
  DropdownSeparator,
} from '../../shared/primitives/dropdown/index.js';
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
  const visibleGroups = groups.filter((group) => group.length > 0);
  if (visibleGroups.length === 0) return null;
  return (
    <Dropdown
      disabled={disabled}
      triggerMode="hover"
      className="ov-file-preview__menu-surface"
      menuClassName="ov-file-preview__menu"
      trigger={
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
      }
    >
      {visibleGroups.map((group, index) => (
        <Fragment key={index}>
          {index > 0 ? <DropdownSeparator /> : null}
          {group.map((action) => (
            <DropdownItem
              key={action.key}
              className={['ov-file-preview__menu-item', action.className]
                .filter(Boolean)
                .join(' ')}
              disabled={disabled || pending || action.disabled}
              onClick={() => onSelect(action)}
            >
              <span className="ov-file-preview__action-icon" aria-hidden="true">
                {action.icon}
              </span>
              {action.label}
            </DropdownItem>
          ))}
        </Fragment>
      ))}
    </Dropdown>
  );
}
