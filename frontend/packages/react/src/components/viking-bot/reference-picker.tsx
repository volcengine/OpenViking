import { Input } from '../../shared/primitives/input/input.js';
import { Button } from '../../shared/primitives/button/button.js';
import { Empty } from '../../shared/primitives/empty/empty.js';
import { orderContextRoots } from '../../shared/context-tree-root-order.js';
import type { Locale } from '../../shared/locale.js';
import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from 'react';
import type { SenderProps } from '@ark-design/ui/sender';
import { usePortalTheme } from '../../shared/primitives/tooltip/use-portal-theme.js';
import { FileTree } from '../file-tree/index.js';
import type { FileTreeHandle } from '../file-tree/index.js';
import SearchIcon from '../../../../icons/svg/scope-switcher/search.svg?react';
import type { VikingBotLabels } from './model.js';
import type {
  VikingBotReference,
  VikingBotReferences,
} from './capabilities.js';
import { resolveVikingBotLabels } from './labels.js';
import { useMentionPosition } from './use-mention-position.js';
import { useReferenceNavigation } from './use-reference-navigation.js';

export type MentionConfig = NonNullable<
  NonNullable<SenderProps['richTextAreaProps']>['mentionConfig']
>;
export type MentionEditor = Parameters<
  NonNullable<MentionConfig['render']>
>[0]['editor'];

/** The same host-owned tree serves the toolbar and Sender's mention extension. */
export function ReferencePicker({
  config,
  locale,
  query: externalQuery,
  editor,
  onSelect,
  labels: overrides,
}: {
  config: VikingBotReferences;
  locale: Locale;
  query?: string;
  editor?: MentionEditor;
  onSelect: (reference: VikingBotReference) => void;
  labels?: VikingBotLabels;
}) {
  const labels = resolveVikingBotLabels(locale, overrides);
  const [search, setSearch] = useState('');
  const [surface, setSurface] = useState<HTMLDivElement | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const tree = useRef<FileTreeHandle>(null);
  const anchor = useMemo(
    () => ({ current: editor?.view.dom ?? null }),
    [editor],
  );
  usePortalTheme(!!editor, anchor, surface);
  const query = externalQuery ?? search;
  const position = useMentionPosition(editor, query, surface);
  const {
    onOpenChange,
    onQueryChange,
    onExpand,
    onLoadMore,
    onRetry,
    isLoading,
    error,
  } = config;
  const loadMore = useReferenceAction(onLoadMore);
  const retry = useReferenceAction(onRetry);
  const queryChange = useRef(onQueryChange);
  const remoteSearch = Boolean(onQueryChange);
  const id = useId();
  const rows = useMemo(() => {
    if (!query) return orderContextRoots(config.rows);
    return remoteSearch
      ? config.rows
      : config.rows.filter(
          (row) =>
            row.kind === 'entry' &&
            `${row.name} ${row.uri}`
              .toLowerCase()
              .includes(query.toLowerCase()),
        );
  }, [config.rows, query, remoteSearch]);
  const activeId = useReferenceNavigation({
    config: { ...config, onLoadMore: loadMore, onRetry: retry },
    rows,
    query,
    editor,
    input: searchInput,
    tree,
    onSelect,
  });
  useEffect(() => {
    queryChange.current = onQueryChange;
  }, [onQueryChange]);
  useEffect(() => {
    onOpenChange?.(true);
    return () => onOpenChange?.(false);
  }, [onOpenChange]);
  // Sender reports its query only through render(). Notify the host after commit,
  // rather than updating the host while its editor is rendering.
  useEffect(() => {
    queryChange.current?.(query);
  }, [query, remoteSearch]);
  return (
    <div
      ref={setSurface}
      role="presentation"
      className={`ov-viking-bot__reference-picker${editor ? ' ov-viking-bot__reference-picker--mention' : ''}`}
      style={editor ? position : undefined}
      onMouseDown={editor ? (event) => event.preventDefault() : undefined}
    >
      {editor ? null : (
        <Input
          ref={searchInput}
          className="ov-viking-bot__reference-search"
          type="search"
          prefix={<SearchIcon width={14} height={14} />}
          aria-label={labels.searchReferences}
          placeholder={labels.searchReferences}
          aria-controls={id}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      )}
      {error ? (
        <div className="ov-viking-bot__reference-error">
          <span role="alert">{error}</span>
          {retry ? (
            <Button size="mini" onClick={() => void retry(undefined)}>
              {labels.retry}
            </Button>
          ) : null}
        </div>
      ) : (
        <FileTree
          id={id}
          ref={tree}
          className="ov-viking-bot__reference-results"
          locale={locale}
          aria-label={labels.reference}
          rows={rows}
          operations={{
            loadMore: loadMore && ((row) => loadMore(row.parentId)),
            retry: retry && ((row) => retry(row.parentId)),
          }}
          search={false}
          refresh={false}
          isReady={!isLoading}
          selectedId={activeId}
          onSelect={onSelect}
          onToggleExpand={onExpand && ((row) => onExpand(row.uri))}
          renderRowTooltip={() => null}
          getRowAttributes={(row) => ({
            className:
              row.kind !== 'entry' && row.id === activeId
                ? 'ov-viking-bot__reference-active'
                : undefined,
          })}
          loadingFallback={<p role="status">{labels.loading}</p>}
          emptyFallback={
            <Empty
              size="compact"
              illustration="search"
              title={labels.emptyReferences}
            />
          }
          labels={{
            expand: labels.expandReference,
            collapse: labels.collapseReference,
          }}
        />
      )}
    </div>
  );
}

// The editor and search field handle Enter outside FileTree. Share the pending
// actions with its buttons so either input path can only start one request.
function useReferenceAction<T>(action?: (id: T) => unknown) {
  const pending = useRef(new Set<T>());
  const run = useCallback(
    async (id: T) => {
      if (!action || pending.current.has(id)) return;
      pending.current.add(id);
      try {
        await action(id);
      } catch {
        // The host supplies the error state for its action.
      } finally {
        pending.current.delete(id);
      }
    },
    [action],
  );
  return action ? run : undefined;
}
