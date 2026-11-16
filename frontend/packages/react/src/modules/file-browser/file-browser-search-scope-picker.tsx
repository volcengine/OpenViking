import { useId, useRef, useState } from 'react';
import FolderIcon from '../../../../icons/svg/file-browser/search-folder-open.svg?react';
import ChevronIcon from '../../../../icons/svg/file-browser/search-chevron-down.svg?react';
import treeEn from '../../components/file-tree/locales/en-US.js';
import treeZh from '../../components/file-tree/locales/zh-CN.js';
import type { OvRequestOptions } from '../../client/types.js';
import { useLocale } from '../../shared/locale-context.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import { nameFromUri } from './fs-model.js';
import { useSearchScopePage } from './use-search-scope-page.js';
import type { ScopeOption } from './search-types.js';

interface FileBrowserSearchScopePickerProps {
  scopes: readonly ScopeOption[];
  value: readonly string[];
  text: ResolvedLabels;
  requestOptions?: OvRequestOptions;
  onSelect: (uri: string) => void;
}

export function FileBrowserSearchScopePicker(
  props: FileBrowserSearchScopePickerProps,
) {
  return (
    <ScopePicker
      key={props.scopes.map(({ uri }) => uri).join('\n')}
      {...props}
    />
  );
}

function ScopePicker({
  scopes,
  value,
  text,
  requestOptions,
  onSelect,
}: FileBrowserSearchScopePickerProps) {
  const treeText = useLocale() === 'zh-CN' ? treeZh : treeEn;
  const id = useId();
  const [open, setOpen] = useState(false);
  const [parents, setParents] = useState<string[]>([]);
  const button = useRef<HTMLButtonElement>(null);
  const uri = parents.at(-1);
  const {
    page: current,
    load,
    cancel,
  } = useSearchScopePage(uri, requestOptions);
  const selected = value.filter(
    (item) => !scopes.some((scope) => scope.uri === item),
  );

  function close() {
    cancel();
    setParents([]);
    setOpen(false);
    button.current?.focus();
  }

  function browse(target: string) {
    setParents([...parents, target]);
    load(target);
  }

  function back() {
    const next = parents.slice(0, -1);
    setParents(next);
    const target = next.at(-1);
    if (target) load(target);
    else cancel();
  }

  return (
    <div className="ov-file-browser-search__scope-picker">
      <button
        ref={button}
        type="button"
        className="ov-file-browser-search__scope-trigger"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => (open ? close() : setOpen(true))}
      >
        <FolderIcon aria-hidden="true" />
        <span className="ov-file-browser-search__scope-trigger-label">
          {text.searchPickScope}
        </span>
        <ChevronIcon aria-hidden="true" />
      </button>
      {selected.length > 0 ? (
        <ul className="ov-file-browser-search__options">
          {selected.map((item) => (
            <li key={item}>
              <button
                type="button"
                className="ov-file-browser-search__scope-choice"
                aria-pressed="true"
                title={item}
                onClick={() => onSelect(item)}
              >
                <span>{item}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {open ? (
        <div
          id={id}
          className="ov-file-browser-search__scope-menu"
          role="group"
          aria-label={text.searchPickScope}
        >
          {uri ? (
            <div className="ov-file-browser-search__scope-current">
              <button
                type="button"
                className="ov-file-browser-search__scope-choice"
                onClick={back}
                aria-label={parents.at(-2) ?? text.searchScopes}
              >
                ←
              </button>
              <button
                type="button"
                className="ov-file-browser-search__scope-choice"
                aria-pressed={value.includes(uri)}
                title={uri}
                onClick={() => {
                  onSelect(uri);
                  close();
                }}
              >
                <span>{nameFromUri(uri)}</span>
              </button>
            </div>
          ) : null}
          {(!uri
            ? scopes.map((scope) => scope.uri)
            : (current?.children ?? [])
          ).map((directory) => (
            <button
              key={directory}
              type="button"
              className="ov-file-browser-search__scope-choice"
              title={directory}
              onClick={() => browse(directory)}
            >
              <FolderIcon aria-hidden="true" />
              <span>{uri ? nameFromUri(directory) : directory}</span>
              <ChevronIcon aria-hidden="true" />
            </button>
          ))}
          {current?.loading ? <p role="status">{text.loading}</p> : null}
          {current?.error ? (
            <button
              type="button"
              className="ov-file-browser-search__scope-choice"
              onClick={() => load(current.uri, current)}
            >
              {text.retry}
            </button>
          ) : null}
          {current?.hasMore ? (
            <button
              type="button"
              className="ov-file-browser-search__scope-choice"
              onClick={() => load(current.uri, current)}
            >
              {text.tree?.loadMore ?? treeText.loadMore}
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
