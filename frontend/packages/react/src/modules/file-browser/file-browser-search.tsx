import { useState, type RefObject } from 'react';
import { useOvClient } from '../../client/provider.js';
import type { OvRequestOptions } from '../../client/types.js';
import { Button } from '../../shared/primitives/button/button.js';
import { Input } from '../../shared/primitives/input/input.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
import type { Locale } from '../../shared/locale.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import { useFsSearch } from './use-fs-search.js';

interface FileBrowserSearchProps {
  open: boolean;
  rootUri: string;
  triggerRef: RefObject<HTMLElement | null>;
  locale: Locale;
  text: ResolvedLabels;
  requestOptions?: OvRequestOptions;
  onClose: () => void;
}

/**
 * TODO: 占位搜索弹窗，目前只提交关键词并展示原生接口结果。
 * 后续补齐筛选、分页、结果定位和快捷键；此视图仅供 FileBrowser 内部使用。
 * Placeholder: query submission and results only; richer search interactions follow.
 */
export function FileBrowserSearch({
  open,
  rootUri,
  triggerRef,
  locale,
  text,
  requestOptions,
  onClose,
}: FileBrowserSearchProps) {
  const client = useOvClient();
  const [draft, setDraft] = useState('');
  const [request, setRequest] = useState({ query: '', generation: 0 });
  const search = useFsSearch({
    client,
    query: open ? request.query : '',
    targetUri: rootUri,
    generation: request.generation,
    requestOptions,
  });
  const close = () => {
    setDraft('');
    setRequest({ query: '', generation: 0 });
    onClose();
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
      <h2 className="ov-file-browser-search__title">{text.searchTitle}</h2>
      <search>
        <form
          className="ov-file-browser-search__form"
          onSubmit={(event) => {
            event.preventDefault();
            const query = draft.trim();
            if (!query || !client || search.loading) return;
            setRequest((current) => ({
              query,
              generation: current.generation + 1,
            }));
          }}
        >
          <Input
            data-autofocus
            required
            type="search"
            name="query"
            aria-label={text.searchTitle}
            placeholder={text.searchPlaceholder}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <Button
            type="primary"
            htmlType="submit"
            loading={search.loading}
            disabled={!client}
          >
            {text.searchAction}
          </Button>
        </form>
      </search>
      <div
        className="ov-file-browser-search__results"
        aria-busy={search.loading}
      >
        {search.loading ? (
          <p role="status">{text.searchLoading}</p>
        ) : search.error ? (
          <div role="alert" className="ov-file-browser-search__error">
            <p>{text.searchFailed}</p>
            <p>{search.error.message}</p>
          </div>
        ) : search.results === null ? (
          <p role="status">{text.searchHint}</p>
        ) : search.results.length === 0 ? (
          <p role="status">{text.searchEmpty}</p>
        ) : (
          <ul className="ov-file-browser-search__list">
            {search.results.map((entry) => (
              <li className="ov-file-browser-search__result" key={entry.uri}>
                <span className="ov-file-browser-search__uri">{entry.uri}</span>
                {entry.abstract ? (
                  <p className="ov-file-browser-search__summary">
                    {entry.abstract}
                  </p>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Modal>
  );
}
