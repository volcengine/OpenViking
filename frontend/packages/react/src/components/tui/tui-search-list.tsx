import { useState } from 'react';

import type { TuiSearchListContent, TuiSearchResultItem } from './types.js';
import { TuiUriLink, TuiUriText } from './tui-uri-text.js';

export interface TuiSearchListProps {
  content: TuiSearchListContent;
  onOpenUri?: (uri: string) => void;
  noResultLabel: string;
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

function ResourceUri({ text }: { text: string }) {
  const boundary = text.lastIndexOf('/');
  return boundary > 'viking://'.length ? (
    <span className="ov-tui__search-uri">
      <span className="ov-tui__search-uri-prefix">
        {text.slice(0, boundary + 1)}
      </span>
      <span className="ov-tui__search-uri-suffix">
        {text.slice(boundary + 1)}
      </span>
    </span>
  ) : (
    <span className="ov-tui__search-uri-full">{text}</span>
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
            {item.uri ? (
              <TuiUriLink
                uri={item.uri}
                onOpenUri={onOpenUri}
                className="ov-tui__link ov-tui__search-open"
              >
                <ResourceUri text={item.uri} />
              </TuiUriLink>
            ) : (
              <TuiUriText
                text={item.title}
                mode="literal"
                onOpenUri={onOpenUri}
              />
            )}
          </span>
          {item.levelLabel ? (
            <span className="ov-tui__search-level">{item.levelLabel}</span>
          ) : null}
        </span>
        {item.preview ? (
          <span className="ov-tui__search-preview">
            <TuiUriText
              text={item.preview}
              mode="prose"
              onOpenUri={onOpenUri}
            />
          </span>
        ) : null}
        {item.metaLabel ? (
          <span className="ov-tui__search-meta">
            <TuiUriText
              text={item.metaLabel}
              mode="prose"
              onOpenUri={onOpenUri}
            />
          </span>
        ) : null}
      </span>
    </>
  );
}

/** 检索结果列表。Search result list. */
export function TuiSearchList({
  content,
  onOpenUri,
  noResultLabel,
}: TuiSearchListProps) {
  return (
    <div className="ov-tui__search">
      {content.summary ? (
        <div className="ov-tui__search-summary">
          {typeof content.summary === 'string' ? (
            <TuiUriText
              text={content.summary}
              mode="prose"
              onOpenUri={onOpenUri}
            />
          ) : (
            content.summary
          )}
        </div>
      ) : null}
      {content.items.length ? (
        <div className="ov-tui__search-items">
          {content.items.map((item) => (
            <article key={item.id} className="ov-tui__search-item">
              <SearchRow item={item} onOpenUri={onOpenUri} />
            </article>
          ))}
        </div>
      ) : (
        <div className="ov-tui__no-result">{noResultLabel}</div>
      )}
    </div>
  );
}
