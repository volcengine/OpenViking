import type { RefObject } from 'react';
import { FileBrowserSearchModeMenu } from './file-browser-search-mode-menu.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/** Result count, current retrieval mode and its shared selection control. */
export interface FileBrowserSearchModeRowProps {
  mode: SearchMode;
  count: number;
  text: ResolvedLabels;
  /** Search input keeps focus when selecting a mode unmounts the results row. */
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
      <span>{text.searchResultCount?.(count)}</span>
      <span aria-hidden="true">・</span>
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
