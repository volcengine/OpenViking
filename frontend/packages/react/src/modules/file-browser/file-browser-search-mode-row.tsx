import { useState } from 'react';
import { FileBrowserSearchModeMenu } from './file-browser-search-mode-menu.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

export interface FileBrowserSearchModeRowProps {
  mode: SearchMode;
  count: number;
  text: ResolvedLabels;
  onSelectMode: (mode: SearchMode) => void;
}

export function FileBrowserSearchModeRow({
  mode,
  count,
  text,
  onSelectMode,
}: FileBrowserSearchModeRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="ov-file-browser-search__summary">
      <span>{text.searchResultCount?.(count)}</span>
      <span aria-hidden="true">・</span>
      <span>{text.searchVia}</span>
      <span className="ov-file-browser-search__mode-chip">{mode}</span>
      <span className="ov-file-browser-search__switch-wrap">
        <button
          type="button"
          className="ov-file-browser-search__switch"
          onClick={() => setMenuOpen((value) => !value)}
        >
          {text.searchSwitchMode}
        </button>
        <FileBrowserSearchModeMenu
          open={menuOpen}
          value={mode}
          text={text}
          onSelect={(next) => {
            setMenuOpen(false);
            onSelectMode(next);
          }}
          onClose={() => setMenuOpen(false)}
        />
      </span>
    </div>
  );
}
