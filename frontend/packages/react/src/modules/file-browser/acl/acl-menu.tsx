import Chevron from '../../../../../icons/svg/scope-switcher/chevron-down.svg?react';
import Check from '../../../../../icons/svg/scope-switcher/check.svg?react';
import {
  Dropdown,
  DropdownItem,
  DropdownSeparator,
} from '../../../shared/primitives/dropdown/index.js';
import type { FileBrowserAclLevel } from '../acl-types.js';
import { LEVELS } from './acl-model.js';
import { levelLabel, type AclText } from './acl-labels.js';

export function AclMenu({
  level,
  text,
  name,
  disabled,
  onSelectCurrent,
  onChange,
  onRemove,
}: {
  level: FileBrowserAclLevel;
  text: AclText;
  name: string;
  disabled?: boolean;
  onSelectCurrent?: boolean;
  onChange: (level: FileBrowserAclLevel) => void;
  onRemove?: () => void;
}) {
  return (
    <Dropdown
      disabled={disabled}
      className="ov-file-browser-acl-menu"
      trigger={
        <button
          type="button"
          className="ov-file-browser-acl__level"
          aria-label={`${name}: ${levelLabel(level, text)}`}
          disabled={disabled}
        >
          {levelLabel(level, text)}
          <Chevron aria-hidden="true" width={12} height={12} />
        </button>
      }
    >
      {LEVELS.map((value) => (
        <DropdownItem
          key={value}
          role="menuitemradio"
          aria-checked={level === value}
          onClick={() => {
            if (value !== level || onSelectCurrent) onChange(value);
          }}
        >
          <span className="ov-file-browser-acl__check">
            {level === value ? (
              <Check width={12} height={12} aria-hidden="true" />
            ) : null}
          </span>
          {levelLabel(value, text)}
        </DropdownItem>
      ))}
      {onRemove ? (
        <>
          <DropdownSeparator />
          <DropdownItem
            className="ov-file-browser-acl__remove"
            onClick={onRemove}
          >
            {text.remove}
          </DropdownItem>
        </>
      ) : null}
    </Dropdown>
  );
}
