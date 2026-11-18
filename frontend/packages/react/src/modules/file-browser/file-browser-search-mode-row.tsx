import type { RefObject } from 'react';
import { FileBrowserSearchModeMenu } from './file-browser-search-mode-menu.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/** Current retrieval mode, its selection control and the count when there are hits. */
export interface FileBrowserSearchModeRowProps {
  mode: SearchMode;
  count?: number;
  text: ResolvedLabels;
  /** Search input receives focus after the user selects a mode. */
  fallbackFocusRef?: RefObject<HTMLElement | null>;
  onSelectMode: (mode: SearchMode) => void;
}

export function FileBrowserSearchModeRow({
  mode,
  count,
  text,
  fallbackFocusRef,
  onSelectMode,
}: FileBrowserSearchModeRowProps) {
  return (
    <div className="ov-file-browser-search__summary">
      {count === undefined ? null : (
        <>
          <span>{text.searchResultCount?.(count)}</span>
          <span aria-hidden="true">・</span>
        </>
      )}
      <span className="ov-file-browser-search__mode-chip">{mode}</span>
      <span>{text.searchVia}</span>
      <FileBrowserSearchModeMenu
        value={mode}
        text={text}
        fallbackFocusRef={fallbackFocusRef}
        onSelect={onSelectMode}
      />
    </div>
  );
}
