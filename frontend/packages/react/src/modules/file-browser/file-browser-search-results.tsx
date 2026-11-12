import { DocumentIcon } from '../../icons/index.js';
import { normalizeResourceUri } from './fs-uri.js';
import type { FsEntry } from './fs-model.js';

export function splitSearchUri(uri: string) {
  const value = normalizeResourceUri(uri);
  const index = value.lastIndexOf('/');
  return {
    name: value.slice(index + 1) || value,
    parent: value.endsWith('://') ? '' : value.slice(0, index + 1),
  };
}

function HighlightedName({ name, query }: { name: string; query: string }) {
  const index = query ? name.toLowerCase().indexOf(query.toLowerCase()) : -1;
  if (index < 0) return name;
  return (
    <>
      {name.slice(0, index)}
      <mark>{name.slice(index, index + query.length)}</mark>
      {name.slice(index + query.length)}
    </>
  );
}

export function FileBrowserSearchResults({
  id,
  label,
  entries,
  query,
  activeIndex,
  onHighlight,
  onOpen,
}: {
  id: string;
  label: string;
  entries: readonly FsEntry[];
  query: string;
  activeIndex: number;
  onHighlight: (index: number) => void;
  onOpen: (uri: string) => void;
}) {
  return (
    <ul
      id={id}
      role="listbox"
      aria-label={label}
      className="ov-file-browser-search__list"
    >
      {entries.map((entry, index) => {
        const { name, parent } = splitSearchUri(entry.uri);
        return (
          <li key={entry.uri} role="presentation">
            <button
              id={`${id}-${index}`}
              type="button"
              role="option"
              aria-selected={index === activeIndex}
              tabIndex={-1}
              className="ov-file-browser-search__result"
              title={entry.uri}
              onPointerEnter={(event) => {
                if (event.pointerType !== 'touch') onHighlight(index);
              }}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => onOpen(entry.uri)}
            >
              <DocumentIcon width={16} height={16} aria-hidden="true" />
              <span className="ov-file-browser-search__name">
                <HighlightedName name={name} query={query} />
              </span>
              {parent ? (
                <span className="ov-file-browser-search__path">{parent}</span>
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}
