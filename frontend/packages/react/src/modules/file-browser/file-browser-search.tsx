import { useId, useRef, useState, type RefObject } from 'react';
import SearchIcon from '../../../../icons/svg/scope-switcher/search.svg?react';
import { useOvClient } from '../../client/provider.js';
import type { OvRequestOptions } from '../../client/types.js';
import { Button } from '../../shared/components/button/button.js';
import { Input } from '../../shared/components/input/input.js';
import { Modal } from '../../shared/components/modal/modal.js';
import type { Locale } from '../../shared/locale.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import { FileBrowserSearchPreview } from './file-browser-search-preview.js';
import { FileBrowserSearchResults } from './file-browser-search-results.js';
import type { FsEntry } from './fs-model.js';
import { FILE_SEARCH_LIMIT, useFsSearch } from './use-fs-search.js';
import type { FileBrowserHandle } from './types.js';

interface FileBrowserSearchProps {
  open: boolean;
  rootUri: string;
  triggerRef: RefObject<HTMLElement | null>;
  locale: Locale;
  text: ResolvedLabels;
  requestOptions?: OvRequestOptions;
  onClose: () => void;
  onOpenResource: FileBrowserHandle['openResource'];
}

export function FileBrowserSearch({
  open,
  rootUri,
  triggerRef,
  locale,
  text,
  requestOptions,
  onClose,
  onOpenResource,
}: FileBrowserSearchProps) {
  const client = useOvClient();
  const listId = useId();
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState('');
  const [composing, setComposing] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [highlight, setHighlight] = useState<{
    results: FsEntry[];
    index: number;
  }>();
  const query = open && !composing ? draft.trim() : '';
  const { results, loading, error } = useFsSearch({
    client,
    query,
    targetUri: rootUri,
    generation,
    requestOptions,
  });
  const activeIndex = results === highlight?.results ? highlight.index : 0;
  const active = results?.[activeIndex];
  const close = () => {
    setDraft('');
    setComposing(false);
    setHighlight(undefined);
    onClose();
  };
  const openResult = (uri: string) => {
    close();
    void onOpenResource(uri).catch(() => {});
  };
  const retry = () => {
    inputRef.current?.focus();
    setGeneration((value) => value + 1);
  };

  return (
    <Modal
      open={open}
      title={text.searchTitle}
      locale={locale}
      triggerRef={triggerRef}
      className="ov-file-browser-search"
      onClose={close}
    >
      <div className="ov-file-browser-search__layout">
        <section className="ov-file-browser-search__panel">
          <form
            role="search"
            aria-label={text.searchAction}
            onSubmit={(event) => {
              event.preventDefault();
              if (active) openResult(active.uri);
              else if (error) retry();
            }}
          >
            <Input
              ref={inputRef}
              className="ov-file-browser-search__input"
              data-autofocus
              type="search"
              role="combobox"
              name="query"
              aria-label={text.searchTitle}
              aria-autocomplete="list"
              aria-expanded={!!active}
              aria-controls={active ? listId : undefined}
              aria-activedescendant={
                active ? `${listId}-${activeIndex}` : undefined
              }
              placeholder={text.searchTitle}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onCompositionStart={() => setComposing(true)}
              onCompositionEnd={(event) => {
                setDraft(event.currentTarget.value);
                setComposing(false);
              }}
              prefix={<SearchIcon width={16} height={16} />}
              suffix={
                <button
                  className="ov-file-browser-search__dismiss"
                  type="button"
                  aria-label={text.searchDismiss}
                  onClick={close}
                >
                  <kbd>{text.searchDismiss}</kbd>
                </button>
              }
              onKeyDown={(event) => {
                if (composing || event.nativeEvent.isComposing) {
                  if (event.key === 'Escape') event.stopPropagation();
                  return;
                }
                if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp')
                  return;
                if (!results?.length) return;
                event.preventDefault();
                const step = event.key === 'ArrowDown' ? 1 : -1;
                const index =
                  (activeIndex + step + results.length) % results.length;
                setHighlight({ results, index });
                inputRef.current?.ownerDocument
                  .getElementById(`${listId}-${index}`)
                  ?.scrollIntoView?.({ block: 'nearest' });
              }}
            />
          </form>
          <div className="ov-file-browser-search__results" aria-busy={loading}>
            {loading ? (
              <p className="ov-file-browser-search__status" role="status">
                {text.searchLoading}
              </p>
            ) : error ? (
              <div className="ov-file-browser-search__status">
                <div className="ov-file-browser-search__error" role="alert">
                  <p>{text.searchFailed}</p>
                  <p>{error.message}</p>
                </div>
                <Button size="small" onClick={retry}>
                  {text.retry}
                </Button>
              </div>
            ) : results?.length ? (
              <>
                <p className="ov-file-browser-search__count" role="status">
                  {text.formatSearchResults(FILE_SEARCH_LIMIT)}
                </p>
                <FileBrowserSearchResults
                  id={listId}
                  label={text.searchTitle}
                  entries={results}
                  query={query}
                  activeIndex={activeIndex}
                  onHighlight={(index) => setHighlight({ results, index })}
                  onOpen={openResult}
                />
              </>
            ) : (
              <p className="ov-file-browser-search__status" role="status">
                {results ? text.searchEmpty : text.searchHint}
              </p>
            )}
          </div>
        </section>
        {active ? (
          <FileBrowserSearchPreview
            client={client}
            entry={active}
            requestOptions={requestOptions}
            text={text}
          />
        ) : null}
      </div>
    </Modal>
  );
}
