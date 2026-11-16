import { useState } from 'react';

import type {
  CommandConsoleSearchListContent,
  CommandConsoleSearchResultItem,
} from './types.js';
import {
  CommandConsoleUriLink,
  CommandConsoleUriText,
} from './command-console-uri-text.js';

export interface CommandConsoleSearchListProps {
  content: CommandConsoleSearchListContent;
  onOpenUri?: (uri: string) => void;
  noResultLabel: string;
}

function ScoreDetails({ item }: { item: CommandConsoleSearchResultItem }) {
  const [hovered, setHovered] = useState(false);
  const [pinned, setPinned] = useState(false);
  if (!item.scoreDetails) {
    return (
      <span className="ov-command-console__search-score">
        {item.scoreLabel}
      </span>
    );
  }

  const open = hovered || pinned;

  return (
    <span className="ov-command-console__search-score-wrap">
      <button
        type="button"
        className="ov-command-console__search-score"
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
        <span role="tooltip" className="ov-command-console__score-details">
          {item.scoreDetails}
        </span>
      ) : null}
    </span>
  );
}

function ResourceUri({ text }: { text: string }) {
  const boundary = text.lastIndexOf('/');
  return boundary > 'viking://'.length ? (
    <span className="ov-command-console__search-uri">
      <span className="ov-command-console__search-uri-prefix">
        {text.slice(0, boundary + 1)}
      </span>
      <span className="ov-command-console__search-uri-suffix">
        {text.slice(boundary + 1)}
      </span>
    </span>
  ) : (
    <span className="ov-command-console__search-uri-full">{text}</span>
  );
}

function SearchRow({
  item,
  onOpenUri,
}: {
  item: CommandConsoleSearchResultItem;
  onOpenUri?: (uri: string) => void;
}) {
  return (
    <>
      {item.scoreLabel || item.typeLabel ? (
        <span className="ov-command-console__search-metrics">
          {item.scoreLabel ? <ScoreDetails item={item} /> : null}
          {item.typeLabel ? (
            <span className="ov-command-console__search-type">
              {item.typeLabel}
            </span>
          ) : null}
        </span>
      ) : null}
      <span className="ov-command-console__search-body">
        <span className="ov-command-console__search-main">
          <span
            className="ov-command-console__search-title"
            title={item.uri ?? item.title}
          >
            {item.uri ? (
              <CommandConsoleUriLink
                uri={item.uri}
                onOpenUri={onOpenUri}
                className="ov-command-console__link ov-command-console__search-open"
              >
                <ResourceUri text={item.uri} />
              </CommandConsoleUriLink>
            ) : (
              <CommandConsoleUriText
                text={item.title}
                mode="literal"
                onOpenUri={onOpenUri}
              />
            )}
          </span>
          {item.levelLabel ? (
            <span className="ov-command-console__search-level">
              {item.levelLabel}
            </span>
          ) : null}
        </span>
        {item.preview ? (
          <span className="ov-command-console__search-preview">
            <CommandConsoleUriText
              text={item.preview}
              mode="prose"
              onOpenUri={onOpenUri}
            />
          </span>
        ) : null}
        {item.metaLabel ? (
          <span className="ov-command-console__search-meta">
            <CommandConsoleUriText
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
export function CommandConsoleSearchList({
  content,
  onOpenUri,
  noResultLabel,
}: CommandConsoleSearchListProps) {
  return (
    <div className="ov-command-console__search">
      {content.summary ? (
        <div className="ov-command-console__search-summary">
          {typeof content.summary === 'string' ? (
            <CommandConsoleUriText
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
        <div className="ov-command-console__search-items">
          {content.items.map((item) => (
            <article key={item.id} className="ov-command-console__search-item">
              <SearchRow item={item} onOpenUri={onOpenUri} />
            </article>
          ))}
        </div>
      ) : (
        <div className="ov-command-console__no-result">{noResultLabel}</div>
      )}
    </div>
  );
}
