import { useState } from 'react';

import type { TuiSearchListContent, TuiSearchResultItem } from './types.js';

export interface TuiSearchListProps {
  content: TuiSearchListContent;
  onOpenUri?: (uri: string) => void;
}

function ScoreDetails({ item }: { item: TuiSearchResultItem }) {
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  if (!item.scoreDetails) {
    return <span className="ov-tui__search-score">{item.scoreLabel}</span>;
  }

  const open = hovered || pinned;

  return (
    <span className="ov-tui__search-score-wrap">
      <button
        type="button"
        className="ov-tui__search-score"
        aria-expanded={open}
        onClick={() => setPinned((current) => !current)}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocus={() => setHovered(true)}
        onBlur={() => {
          setHovered(false);
          setPinned(false);
        }}
      >
        {item.scoreLabel}
      </button>
      {open ? (
        <span role="tooltip" className="ov-tui__score-details">
          {item.scoreDetails}
        </span>
      ) : null}
    </span>
  );
}

function SearchRow({
  item,
  onOpenUri,
}: {
  item: TuiSearchResultItem;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <>
      {item.scoreLabel || item.typeLabel ? (
        <span className="ov-tui__search-metrics">
          {item.scoreLabel ? <ScoreDetails item={item} /> : null}
          {item.typeLabel ? (
            <span className="ov-tui__search-type">{item.typeLabel}</span>
          ) : null}
        </span>
      ) : null}
      <span className="ov-tui__search-body">
        <span className="ov-tui__search-main">
          <span className="ov-tui__search-title" title={item.uri ?? item.title}>
            {item.uri && onOpenUri ? (
              <button
                type="button"
                className="ov-tui__link"
                onClick={() => onOpenUri(item.uri ?? '')}
              >
                {item.title}
              </button>
            ) : (
              item.title
            )}
          </span>
          {item.levelLabel ? (
            <span className="ov-tui__search-level">{item.levelLabel}</span>
          ) : null}
        </span>
        {item.preview ? (
          <span className="ov-tui__search-preview">{item.preview}</span>
        ) : null}
        {item.metaLabel ? (
          <span className="ov-tui__search-meta">{item.metaLabel}</span>
        ) : null}
      </span>
    </>
  );
}

/** 检索结果列表。Search result list. */
export function TuiSearchList({ content, onOpenUri }: TuiSearchListProps) {
  return (
    <div className="ov-tui__search">
      {content.summary ? (
        <div className="ov-tui__search-summary">{content.summary}</div>
      ) : null}
      {content.items.length ? (
        <div className="ov-tui__search-items">
          {content.items.map((item) => (
            <div key={item.id} className="ov-tui__search-item">
              <SearchRow item={item} onOpenUri={onOpenUri} />
            </div>
          ))}
        </div>
      ) : (
        <div className="ov-tui__muted">—</div>
      )}
    </div>
  );
}
