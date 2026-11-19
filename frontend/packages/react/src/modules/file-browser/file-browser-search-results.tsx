import { useExposure } from '../../shared/analytics/use-exposure.js';
import SearchEmptyArt from '../../../../icons/svg/file-browser/search-empty.svg?react';
import { Button } from '../../shared/primitives/button/button.js';
import { Empty } from '../../shared/primitives/empty/empty.js';
import { FileBrowserSearchRow } from './file-browser-search-row.js';
import type { SearchHit } from './search-types.js';
import type { ResolvedLabels } from './file-browser-labels.js';

/** Search results, with empty, loading, and error states. */
export interface FileBrowserSearchResultsProps {
  open: boolean;
  listId: string;
  activeIndex: number;
  query: string;
  onHighlight: (index: number) => void;
  onRetry: () => void;
  hits: SearchHit[];
  loading: boolean;
  error: Error | null;
  /** 是否已经搜过；`false` 时显示空态。Whether a search has run; `false` shows the empty state. */
  searched: boolean;
  text: ResolvedLabels;
  onOpen: (uri: string) => void;
}

/** 骨架条的宽度，按设计稿的八条。Widths of the eight skeleton bars from the design. */
const SKELETON_WIDTHS = [368, 348, 319, 359, 335, 316, 368, 351];

export function FileBrowserSearchResults({
  open,
  listId,
  activeIndex,
  query,
  onHighlight,
  onRetry,
  hits,
  loading,
  error,
  searched,
  text,
  onOpen,
}: FileBrowserSearchResultsProps) {
  const exposure = useExposure(
    hits.length
      ? 'file_browser_search_results_show'
      : 'file_browser_search_empty_show',
    hits,
    undefined,
    open && searched && !loading && !error,
  );
  if (loading && !hits.length) {
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

  const failure = error ? (
    <>
      <div role="alert" className="ov-file-browser-search__error">
        <p>{text.searchFailed}</p>
        <p>{error.message}</p>
      </div>
      <Button size="small" disabled={loading} onClick={onRetry}>
        {text.retry}
      </Button>
    </>
  ) : null;
  if (failure && !hits.length) return failure;

  if (!searched) {
    return (
      <div className="ov-file-browser-search__empty">
        <SearchEmptyArt
          className="ov-file-browser-search__empty-art"
          aria-hidden="true"
        />
        <div className="ov-file-browser-search__empty-text">
          <p className="ov-file-browser-search__empty-title">
            {text.searchEmptyTitle}
          </p>
          <p className="ov-file-browser-search__empty-hint">
            {text.searchHint}
          </p>
        </div>
      </div>
    );
  }

  if (hits.length === 0) {
    return (
      <Empty
        ref={exposure}
        illustration="search"
        title={text.searchEmpty}
        className="ov-file-browser-search__no-results"
      />
    );
  }

  return (
    <>
      {failure}
      {loading ? <p role="status">{text.searchLoading}</p> : null}
      <ul
        ref={exposure}
        id={listId}
        role="listbox"
        aria-label={text.searchTitle}
        className="ov-file-browser-search__list"
      >
        {hits.map((hit, index) => (
          <FileBrowserSearchRow
            key={`${hit.uri}#${hit.line ?? 0}-${index}`}
            id={`${listId}-${index}`}
            hit={hit}
            query={query}
            active={index === activeIndex}
            onHighlight={() => onHighlight(index)}
            onOpen={onOpen}
          />
        ))}
      </ul>
    </>
  );
}
