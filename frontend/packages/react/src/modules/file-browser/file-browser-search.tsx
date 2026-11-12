import { useEffect, useMemo, useRef, useState } from 'react';
import type { RefObject } from 'react';
import { useOvClient } from '../../client/provider.js';
import type { OvRequestOptions } from '../../client/types.js';
import { Modal } from '../../shared/components/modal/modal.js';
import type { Locale } from '../../shared/locale.js';
import { FileBrowserSearchAdvanced } from './file-browser-search-advanced.js';
import { FileBrowserSearchHeader } from './file-browser-search-header.js';
import { FileBrowserSearchResults } from './file-browser-search-results.js';
import type { ResolvedLabels } from './file-browser-labels.js';
import { buildScopeOptions, flattenResult } from './search-model.js';
import type { SearchFilters, SearchMode } from './search-types.js';
import { DEFAULT_SEARCH_MODE, EMPTY_FILTERS } from './search-types.js';
import { useFsSearch } from './use-fs-search.js';

/** 输入停顿多久后才发请求。How long typing must pause before a request. */
const DEBOUNCE_MS = 400;

interface FileBrowserSearchProps {
  open: boolean;
  rootUri: string;
  triggerRef: RefObject<HTMLElement | null>;
  locale: Locale;
  text: ResolvedLabels;
  requestOptions?: OvRequestOptions;
  /** 高级面板是否展开；由宿主组件持有，左栏漏斗与弹窗内开关共用。 */
  advancedOpen: boolean;
  onToggleAdvanced: (open: boolean) => void;
  /** 打开结果：定位到树并加载预览。Opens a result: locates it in the tree and previews it. */
  onOpenResource: (uri: string) => void;
  onClose: () => void;
}

/**
 * 文件浏览模块的搜索弹窗：四种检索方式共用一套输入与结果列表。
 *
 * 输入即搜（防抖后自动请求），与 studio 一致；没有提交按钮。模式与筛选变化都会
 * 立即重新请求，查询词变化要等防抖。关闭时清空输入与筛选并取消在途请求。
 *
 * The module's search dialog: four retrieval modes share one input and one result
 * list.
 *
 * Search runs as you type (after a debounce), matching studio, with no submit button.
 * A mode or filter change requests immediately; a query change waits for the debounce.
 * Closing clears the input and filters and cancels the in-flight request.
 */
export function FileBrowserSearch({
  open,
  rootUri,
  triggerRef,
  locale,
  text,
  requestOptions,
  advancedOpen,
  onToggleAdvanced,
  onOpenResource,
  onClose,
}: FileBrowserSearchProps) {
  const client = useOvClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');
  const [mode, setMode] = useState<SearchMode>(DEFAULT_SEARCH_MODE);
  const [filters, setFilters] = useState<SearchFilters>(EMPTY_FILTERS);
  const [menuOpen, setMenuOpen] = useState(false);

  const scopes = useMemo(() => buildScopeOptions(rootUri), [rootUri]);
  const search = useFsSearch({
    client,
    mode,
    query: open ? query : '',
    filters,
    rootUri,
    requestOptions,
  });

  // useEffect 必要性：与浏览器定时器同步。输入停顿才发请求是时间维度的行为，
  // 没有对应的元素事件；查询词或开关变化时清除上一个定时器，卸载时同样清除。
  //
  // Syncs with a browser timer. Waiting for typing to pause is a time-based behaviour
  // with no corresponding element event; a query or open change clears the previous
  // timer, as does unmounting.
  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => setQuery(draft), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, open]);

  // 打开时把焦点放进输入框：弹窗一出现就该能直接打字。
  // Moves focus into the input when the dialog opens, so typing can start at once.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // 只清本组件持有的状态；高级面板的开合归宿主，由 `onClose` 一并收起——两处都
  // 写同一个状态时，日后父级改成条件关闭就会与这里的重置打架。
  //
  // Only the state this component owns is cleared here; the advanced panel's open state
  // belongs to the host and collapses through `onClose`. Writing it in both places would
  // clash the moment the parent makes closing conditional.
  const close = () => {
    setDraft('');
    setQuery('');
    setMode(DEFAULT_SEARCH_MODE);
    setFilters(EMPTY_FILTERS);
    setMenuOpen(false);
    onClose();
  };

  const hits = flattenResult(search.result);
  const openResult = (uri: string) => {
    onOpenResource(uri);
    close();
  };

  return (
    <Modal
      open={open}
      title={text.searchTitle}
      locale={locale}
      triggerRef={triggerRef}
      className={[
        'ov-file-browser-search',
        advancedOpen ? 'ov-file-browser-search--advanced' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      onClose={close}
    >
      <FileBrowserSearchHeader
        mode={mode}
        draft={draft}
        text={text}
        inputRef={inputRef}
        advancedOpen={advancedOpen}
        onChange={setDraft}
        onToggleAdvanced={() => onToggleAdvanced(!advancedOpen)}
      />
      <div className="ov-file-browser-search__content">
        <div className="ov-file-browser-search__results">
          <FileBrowserSearchResults
            mode={mode}
            hits={hits}
            loading={search.loading}
            error={search.error}
            searched={search.result !== null}
            text={text}
            menuOpen={menuOpen}
            onToggleMenu={() => setMenuOpen((value) => !value)}
            onCloseMenu={() => setMenuOpen(false)}
            onSelectMode={(next) => {
              setMode(next);
              setMenuOpen(false);
            }}
            onOpen={openResult}
          />
        </div>
        {advancedOpen ? (
          <FileBrowserSearchAdvanced
            mode={mode}
            filters={filters}
            scopes={scopes}
            locale={locale}
            text={text}
            onChange={setFilters}
            onClose={() => onToggleAdvanced(false)}
          />
        ) : null}
      </div>
    </Modal>
  );
}
