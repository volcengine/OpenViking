import { MonitoringFlow } from '../../shared/monitoring/flow-context.js';
import { useTrack } from '../../shared/analytics/use-track.js';
import { useOpenExposure } from '../../shared/analytics/use-exposure.js';
import { useRenderErrors, type ResolvedLabels } from './file-browser-labels.js';
import { ErrorBoundary } from '../../shared/error-boundary/error-boundary.js';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { RefObject } from 'react';
import SearchCloseIcon from '../../../../icons/svg/file-browser/search-close.svg?react';
import { useOvClient } from '../../client/provider.js';
import type { OvRequestOptions } from '../../client/types.js';
import { Modal } from '../../shared/primitives/modal/modal.js';
import type { Locale } from '../../shared/locale.js';
import { FileBrowserSearchAdvanced } from './file-browser-search-advanced.js';
import { FileBrowserSearchHeader } from './file-browser-search-header.js';
import { useSearchHighlight } from './use-search-highlight.js';
import type { FileBrowserHandle } from './types.js';
import { FileBrowserSearchResults } from './file-browser-search-results.js';
import {
  buildScopeOptions,
  isSearchScopeAllowed,
  normalizeFilters,
} from './search-model.js';
import { flattenResult } from './search-hit-order.js';
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
  onOpenResource: FileBrowserHandle['openResource'];
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
  const track = useTrack();
  const exposure = useOpenExposure('file_browser_search_dialog_show', open);
  const client = useOvClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [draft, setDraft] = useState('');
  const [query, setQuery] = useState('');
  const [composing, setComposing] = useState(false);
  const [mode, setMode] = useState<SearchMode>(DEFAULT_SEARCH_MODE);
  const [filters, setFilters] = useState<SearchFilters>(EMPTY_FILTERS);

  const scopes = useMemo(() => buildScopeOptions(rootUri), [rootUri]);

  // 只保留仍在当前候选目录范围内的 scopes。`filters.scopes` 会跨 `rootUri` 变化保留，
  // 若根从 project-a 换成 project-b，勾选状态还指着 project-a——面板显示未勾选，
  // 请求却仍发 `target_uri: ["viking://resources/project-a"]`，结果来自当前树之外。
  // 渲染时算一次即可，不必另存一份 state 再同步。
  //
  // Keep only scopes within the directories offered by the current root. `filters.scopes` survives a
  // `rootUri` change, so after switching from project-a to project-b the selection still
  // names project-a: the panel shows nothing checked while the request still sends
  // `target_uri: ["viking://resources/project-a"]`, returning results from outside the
  // current tree. Computing it during render needs no mirrored state.
  const effectiveFilters = useMemo(() => {
    if (filters.scopes.length === 0) return filters;
    const scopes_ = filters.scopes.filter((uri) =>
      isSearchScopeAllowed(scopes, uri),
    );
    return scopes_.length === filters.scopes.length
      ? filters
      : { ...filters, scopes: scopes_ };
  }, [filters, scopes]);

  const awaitingQuery = !composing && draft.trim() !== query.trim();
  const search = useFsSearch({
    client,
    mode,
    query: !composing && !awaitingQuery ? query : '',
    filters: effectiveFilters,
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
    if (!open || composing) return;
    const timer = setTimeout(() => setQuery(draft), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [draft, open, composing]);

  // 打开时把焦点放进输入框：弹窗一出现就该能直接打字。
  // Moves focus into the input when the dialog opens, so typing can start at once.
  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  // 关闭动画结束后再清本组件持有的状态，淡出过程中保持当前结果。高级面板的开合
  // 归宿主，由 `onClose` 一并收起——两处都写同一个状态时，日后父级改成条件关闭就会
  // 与这里的重置打架。
  //
  // Clears the state this component owns once the exit transition ends, so the results
  // stay put while the dialog fades out. The advanced panel's open state belongs to the
  // host and collapses through `onClose`. Writing it in both places would clash the
  // moment the parent makes closing conditional.
  const reset = () => {
    setDraft('');
    setQuery('');
    setComposing(false);
    setMode(DEFAULT_SEARCH_MODE);
    setFilters(EMPTY_FILTERS);
  };

  const hits = useMemo(() => flattenResult(search.result), [search.result]);
  const openResult = (uri: string) => {
    track('file_browser_search_result_select');
    onClose();
    void onOpenResource(uri).catch(() => {});
  };
  const retry = () => {
    inputRef.current?.focus();
    search.retry();
  };
  const { active, activeIndex, listId, onHighlight, onKeyDown } =
    useSearchHighlight(
      hits,
      composing,
      openResult,
      search.error ? retry : undefined,
    );

  return (
    <Modal
      open={open}
      title={text.searchTitle}
      locale={locale}
      closeLabel={text.searchDismiss}
      closeIcon={<SearchCloseIcon aria-hidden="true" />}
      triggerRef={triggerRef}
      className={`ov-file-browser-search${advancedOpen ? ' ov-file-browser-search--advanced' : ''}`}
      onClose={onClose}
      afterClose={reset}
    >
      <FileBrowserSearchHeader
        mode={mode}
        draft={draft}
        text={text}
        inputRef={inputRef}
        advancedOpen={advancedOpen}
        inputProps={{
          role: 'combobox',
          'aria-autocomplete': 'list',
          'aria-expanded': !!active,
          'aria-controls': active ? listId : undefined,
          'aria-activedescendant': active
            ? `${listId}-${activeIndex}`
            : undefined,
          onKeyDown,
          onCompositionStart: () => setComposing(true),
          onCompositionEnd: (event) => {
            setDraft(event.currentTarget.value);
            setComposing(false);
          },
        }}
        onChange={setDraft}
        onToggleAdvanced={() => onToggleAdvanced(!advancedOpen)}
      />
      <div ref={exposure} className="ov-file-browser-search__content">
        <div className="ov-file-browser-search__results">
          <MonitoringFlow
            operation={search.monitoring}
            unobservedInput={!!search.result && !search.monitoring}
          >
            <ErrorBoundary
              monitoringRegion="search"
              message={useRenderErrors(locale).search}
              resetKeys={[rootUri, query, mode]}
            >
              <FileBrowserSearchResults
                open={open}
                mode={mode}
                hits={hits}
                listId={listId}
                activeIndex={activeIndex}
                query={query.trim()}
                onHighlight={onHighlight}
                onRetry={retry}
                fallbackFocusRef={inputRef}
                loading={search.loading || (awaitingQuery && !!draft.trim())}
                error={search.error}
                searched={search.result !== null}
                text={text}
                onSelectMode={(next) => {
                  setMode(next);
                  // 切模式时把筛选收敛到新模式真正会用的形态，显示与请求才不会脱节。
                  // Collapse the filters to what the new mode actually uses, so the display
                  // and the request cannot drift apart.
                  setFilters((current) => normalizeFilters(current, next));
                }}
                onOpen={openResult}
              />
            </ErrorBoundary>
          </MonitoringFlow>
        </div>
        {advancedOpen ? (
          <FileBrowserSearchAdvanced
            mode={mode}
            filters={effectiveFilters}
            scopes={scopes}
            requestOptions={requestOptions}
            locale={locale}
            text={text}
            onChange={setFilters}
            onClose={() => {
              onToggleAdvanced(false);
              inputRef.current?.focus();
            }}
          />
        ) : null}
      </div>
    </Modal>
  );
}
