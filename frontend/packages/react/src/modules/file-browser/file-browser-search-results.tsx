import { FileBrowserSearchRow } from './file-browser-search-row.js';
import { FileBrowserSearchModeMenu } from './file-browser-search-mode-menu.js';
import type { SearchHit, SearchMode } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/**
 * 结果区：计数行与列表，另有空态、加载骨架与失败三态。
 *
 * 计数行只在有结果时出现——没搜过的时候它没有可数的东西，画一条「检索到 0 条」
 * 反而像搜索失败了。
 *
 * The result area: the count line and the list, plus the empty, loading and failure
 * states.
 *
 * The count line only appears when there are results: before a search there is nothing
 * to count, and "0 results" reads as a failure.
 */
export interface FileBrowserSearchResultsProps {
  mode: SearchMode;
  hits: SearchHit[];
  loading: boolean;
  error: Error | null;
  /** 是否已经搜过；`false` 时显示空态。Whether a search has run; `false` shows the empty state. */
  searched: boolean;
  text: ResolvedLabels;
  menuOpen: boolean;
  onToggleMenu: () => void;
  onCloseMenu: () => void;
  onSelectMode: (mode: SearchMode) => void;
  onOpen: (uri: string) => void;
}

/** 骨架条的宽度，按设计稿的八条。Widths of the eight skeleton bars from the design. */
const SKELETON_WIDTHS = [368, 348, 319, 359, 335, 316, 368, 351];

export function FileBrowserSearchResults({
  mode,
  hits,
  loading,
  error,
  searched,
  text,
  menuOpen,
  onToggleMenu,
  onCloseMenu,
  onSelectMode,
  onOpen,
}: FileBrowserSearchResultsProps) {
  if (loading) {
    return (
      <div className="ov-file-browser-search__skeletons" aria-busy="true">
        <p role="status" className="ov-file-browser-search__sr-only">
          {text.searchLoading}
        </p>
        {SKELETON_WIDTHS.map((width, index) => (
          <div
            className="ov-file-browser-search__skeleton"
            key={`${width}-${index}`}
          >
            <span className="ov-file-browser-search__skeleton-dot" />
            <span
              className="ov-file-browser-search__skeleton-bar"
              style={{ width }}
            />
          </div>
        ))}
      </div>
    );
  }

  if (error) {
    return (
      <div role="alert" className="ov-file-browser-search__error">
        <p>{text.searchFailed}</p>
        <p>{error.message}</p>
      </div>
    );
  }

  if (!searched) {
    return (
      <div className="ov-file-browser-search__empty">
        <p className="ov-file-browser-search__empty-title">
          {text.searchEmptyTitle}
        </p>
        <p className="ov-file-browser-search__empty-hint">{text.searchHint}</p>
      </div>
    );
  }

  if (hits.length === 0) {
    return (
      <p role="status" className="ov-file-browser-search__no-results">
        {text.searchEmpty}
      </p>
    );
  }

  return (
    <>
      <div className="ov-file-browser-search__summary">
        <span>{text.searchResultCount?.(hits.length)}</span>
        <span aria-hidden="true">・</span>
        <span>{text.searchVia}</span>
        <span className="ov-file-browser-search__mode-chip">{mode}</span>
        <span className="ov-file-browser-search__switch-wrap">
          <button
            type="button"
            className="ov-file-browser-search__switch"
            onClick={onToggleMenu}
          >
            {text.searchSwitchMode}
          </button>
          <FileBrowserSearchModeMenu
            open={menuOpen}
            value={mode}
            text={text}
            onSelect={onSelectMode}
            onClose={onCloseMenu}
          />
        </span>
      </div>
      <ul className="ov-file-browser-search__list">
        {hits.map((hit) => (
          <FileBrowserSearchRow
            key={`${hit.uri}#${hit.line ?? 0}`}
            hit={hit}
            onOpen={onOpen}
          />
        ))}
      </ul>
    </>
  );
}
