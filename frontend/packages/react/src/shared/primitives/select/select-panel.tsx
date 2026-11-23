import { useState } from 'react';
import { SelectSearch } from './select-search.js';
import { collectOptions, filterOptions } from './collection.js';
import { SelectContext } from './select-context.js';
import type { SelectProps, SelectValue } from './types.js';
import type { useSelect } from './use-select.js';
import type { useSelectKeyboard } from './use-select-keyboard.js';
import en from './locales/en-US.js';
import zh from './locales/zh-CN.js';

export function SelectPanel<V extends SelectValue>({
  children,
  state,
  keyboard,
  id,
  triggerId,
  multiple,
  searchable,
  searchPlaceholder,
  filterOption = true,
  loading,
  loadingContent,
  searching,
  searchingContent,
  emptyContent,
  onSearch,
  listboxClassName,
  popupFooter,
  locale,
}: Pick<
  SelectProps<V>,
  | 'children'
  | 'multiple'
  | 'searchable'
  | 'searchPlaceholder'
  | 'filterOption'
  | 'loading'
  | 'loadingContent'
  | 'searching'
  | 'searchingContent'
  | 'emptyContent'
  | 'onSearch'
  | 'listboxClassName'
  | 'popupFooter'
> & {
  state: ReturnType<typeof useSelect<V>>;
  keyboard: ReturnType<typeof useSelectKeyboard<V>>;
  id: string;
  triggerId: string;
  locale: 'en-US' | 'zh-CN';
}) {
  const [query, setQuery] = useState('');
  const text = locale === 'zh-CN' ? zh : en;
  const busy = Boolean(loading || searching);
  const progressLabel = searching ? text.searching : text.loading;
  const progressContent = searching ? searchingContent : loadingContent;
  const filtered = filterOption
    ? filterOptions(children, query.trim().toLocaleLowerCase())
    : children;
  const empty = collectOptions(filtered).length === 0;
  return (
    <SelectContext.Provider
      value={{ values: state.values, disabled: false, choose: state.choose }}
    >
      {searchable ? (
        <SelectSearch
          inputRef={state.search}
          listboxId={id}
          label={text.search}
          clearLabel={text.clear}
          placeholder={searchPlaceholder ?? text.search}
          query={query}
          onQueryChange={(next) => {
            setQuery(next);
            onSearch?.(next);
          }}
          onKeyDown={keyboard.searchKey}
        />
      ) : null}
      <div
        ref={state.listbox}
        role="listbox"
        id={id}
        tabIndex={-1}
        aria-labelledby={triggerId}
        aria-multiselectable={multiple || undefined}
        aria-busy={busy || undefined}
        className={['ov-select__listbox', listboxClassName]
          .filter(Boolean)
          .join(' ')}
        onKeyDown={keyboard.listKey}
      >
        {busy ? null : filtered}
      </div>
      {busy ? (
        <div
          className="ov-select__empty"
          role="progressbar"
          aria-label={progressLabel}
        >
          {progressContent === undefined ? progressLabel : progressContent}
        </div>
      ) : null}
      <div className="ov-select__empty" role="status">
        {!busy && empty
          ? emptyContent === undefined
            ? text.empty
            : emptyContent
          : null}
      </div>
      {popupFooter != null ? (
        <div
          ref={state.footer}
          role="presentation"
          onKeyDown={keyboard.footerKey}
        >
          {popupFooter}
        </div>
      ) : null}
    </SelectContext.Provider>
  );
}
