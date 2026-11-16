import type { RefObject } from 'react';
import { Select, SelectOption } from '../../shared/primitives/select/index.js';
import { SEARCH_MODES } from './search-types.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/** The caller owns the current mode, labels and retrieval behavior. */
export function FileBrowserSearchModeMenu({
  value,
  text,
  onSelect,
  fallbackFocusRef,
}: {
  value: SearchMode;
  text: ResolvedLabels;
  fallbackFocusRef?: RefObject<HTMLElement | null>;
  onSelect: (mode: SearchMode) => void;
}) {
  return (
    <Select<SearchMode>
      variant="simple"
      value={[value]}
      aria-label={text.searchSwitchMode}
      onChange={([mode]) => {
        if (mode) {
          fallbackFocusRef?.current?.focus();
          onSelect(mode);
        }
      }}
      popupClassName="ov-file-browser-search__menu"
      renderValue={() => text.searchSwitchMode}
      renderTrigger={({ triggerRef, triggerProps, content }) => (
        <button
          {...triggerProps}
          ref={triggerRef}
          type="button"
          className="ov-file-browser-search__switch"
        >
          {content}
        </button>
      )}
    >
      {SEARCH_MODES.map((mode) => (
        <SelectOption
          key={mode}
          value={mode}
          label={mode}
          className="ov-file-browser-search__menu-item"
        >
          <span className="ov-file-browser-search__menu-text">
            <span className="ov-file-browser-search__menu-name">{mode}</span>
            <span className="ov-file-browser-search__menu-hint">
              {text.searchModeHints[mode]}
            </span>
          </span>
        </SelectOption>
      ))}
    </Select>
  );
}
