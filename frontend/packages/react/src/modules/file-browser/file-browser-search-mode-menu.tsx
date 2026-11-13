import { SEARCH_MODES } from './search-types.js';
import type { SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 模式下拉的内容：四种检索方式各一行，带一句说明，选中项打勾。
 *
 * 浮层本身由结果区的 `Popover` 提供——它挂到 `document.body` 并用视口坐标定位，
 * 结果区裁掉的溢出不会波及菜单。这里只画内容。
 *
 * The mode menu's content: one row per retrieval mode with a one-line description and
 * a check on the current one.
 *
 * The surface comes from the result area's `Popover`, which portals into `document.body`
 * and positions in viewport coordinates, so the results area's clipping cannot reach it.
 * This draws the content only.
 */
export interface FileBrowserSearchModeMenuProps {
  value: SearchMode;
  text: ResolvedLabels;
  onSelect: (mode: SearchMode) => void;
}

export function FileBrowserSearchModeMenu({
  value,
  text,
  onSelect,
}: FileBrowserSearchModeMenuProps) {
  return (
    <div
      className="ov-file-browser-search__menu"
      role="group"
      aria-label={text.searchSwitchMode}
    >
      {SEARCH_MODES.map((mode) => (
        <button
          key={mode}
          type="button"
          aria-pressed={mode === value}
          className="ov-file-browser-search__menu-item"
          onClick={() => onSelect(mode)}
        >
          <span className="ov-file-browser-search__menu-text">
            <span className="ov-file-browser-search__menu-name">{mode}</span>
            <span className="ov-file-browser-search__menu-hint">
              {text.searchModeHints[mode]}
            </span>
          </span>
          {mode === value ? (
            <svg
              aria-hidden="true"
              width={14}
              height={14}
              viewBox="0 0 16 16"
              fill="none"
            >
              <path
                d="M3 8.5 6.5 12 13 4.5"
                stroke="currentColor"
                strokeWidth="1.6"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          ) : null}
        </button>
      ))}
    </div>
  );
}
