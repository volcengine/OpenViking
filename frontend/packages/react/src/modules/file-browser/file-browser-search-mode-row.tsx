import { useState } from 'react';
import { FileBrowserSearchModeMenu } from './file-browser-search-mode-menu.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 结果区顶部的模式行：数量、模式 chip、检索方式说明与切换入口。
 *
 * 顺序与设计稿一致：先给数量，再给当前模式，最后才是切换入口。
 *
 * The mode row above the results: the count, the mode chip, what the mode retrieves
 * and the switch.
 *
 * The order follows the design: the count first, then the current mode, then the
 * switch.
 */
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
      <span className="ov-file-browser-search__mode-chip">{mode}</span>
      <span>{text.searchVia}</span>
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
