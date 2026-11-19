import { useCallback, useEffect, useMemo, useState } from 'react';
import { createOvClient } from '../../client/client.js';
import { OV_PATHS } from '../../client/paths.js';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type {
  VikingBotReference,
  VikingBotReferences,
} from '../../components/viking-bot/index.js';
import type { ConsoleChatOptions } from './types.js';
import { useDirectoryTree } from '../../shared/directory-tree/use-directory-tree.js';
import {
  mentionPattern,
  mentionSearchRows,
  MENTION_ROOT,
} from './mention-search.js';

const emptyReferences: readonly VikingBotReference[] = [];

export function useConversationReferences({
  enabled,
  requestOptions,
  scopeKey,
  selection,
  directoryClient,
}: {
  enabled: boolean;
  requestOptions?: OvRequestOptions;
  scopeKey?: string;
  selection?: ConsoleChatOptions['referenceSelection'];
  directoryClient?: OvClient;
}) {
  const inheritedClient = useOvClient('local');
  const inheritedSignal = useOvSignal();
  const [fallback] = useState(createOvClient);
  const client = directoryClient ?? inheritedClient ?? fallback;
  const {
    baseURL,
    apiKey,
    accountId,
    userId,
    resourceId,
    signal = inheritedSignal,
  } = requestOptions ?? {};
  const scope = useMemo(
    () => ({
      client,
      scopeKey,
      enabled,
      options: { baseURL, apiKey, accountId, userId, resourceId, signal },
    }),
    [
      client,
      baseURL,
      apiKey,
      accountId,
      userId,
      resourceId,
      signal,
      scopeKey,
      enabled,
    ],
  );
  const [selected, setSelected] = useState<{
    scope: typeof scope;
    items: readonly VikingBotReference[];
  }>();
  const onChange = useCallback(
    (items: readonly VikingBotReference[]) => setSelected({ scope, items }),
    [scope],
  );
  const onSelectionChange = enabled ? selection?.onChange : undefined;
  const clearReferences = useCallback(
    (sent?: readonly VikingBotReference[]) => {
      if (onSelectionChange) {
        if (!sent) onSelectionChange([]);
        return;
      }
      setSelected((current) => {
        if (current?.scope !== scope) return current;
        if (!sent) return undefined;
        const items = current.items.filter((item) => !sent.includes(item));
        return items.length === current.items.length
          ? current
          : { scope, items };
      });
    },
    [scope, onSelectionChange],
  );
  const [openCount, setOpenCount] = useState(0);
  const onOpenChange = useCallback((open: boolean) => {
    setOpenCount((count) => (open ? count + 1 : Math.max(0, count - 1)));
  }, []);
  const [query, setQuery] = useState('');
  const keyword = query
    .trim()
    .replace(/^viking:\/\//i, '')
    .split('/')
    .filter(Boolean)
    .join('/');
  const opened = enabled && openCount > 0;
  const tree = useDirectoryTree({
    client: opened ? client : null,
    rootUri: MENTION_ROOT,
    requestOptions: scope.options,
    cacheScopeKey: scopeKey,
  });
  const [search, setSearch] = useState<{
    scope: typeof scope;
    keyword: string;
    rows?: ReturnType<typeof mentionSearchRows>;
    error?: string;
  }>();
  const [attempt, setAttempt] = useState(0);
  const [collapsed, setCollapsed] = useState<{
    result: typeof search;
    uris: Set<string>;
  }>();
  // Picker and scope changes cancel the old request, including its debounce.
  useEffect(() => {
    if (!opened || !keyword) return;
    const controller = new AbortController();
    const requestSignal = signal
      ? AbortSignal.any([signal, controller.signal])
      : controller.signal;
    const timer = setTimeout(() => {
      void scope.client
        .request<{ matches: unknown }>({
          method: 'POST',
          path: OV_PATHS.searchGlob,
          body: {
            uri: MENTION_ROOT,
            pattern: mentionPattern(keyword),
            node_limit: 100,
          },
          options: { ...scope.options, signal: requestSignal },
        })
        .then(({ matches }) => {
          if (requestSignal.aborted) return;
          setSearch({ scope, keyword, rows: mentionSearchRows(matches) });
        })
        .catch((error: unknown) => {
          if (requestSignal.aborted) return;
          setSearch({
            scope,
            keyword,
            error: error instanceof Error ? error.message : String(error),
          });
        });
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [opened, keyword, scope, signal, attempt]);
  if (!enabled) return { references: undefined, clearReferences };

  const result =
    search?.scope === scope && search.keyword === keyword ? search : undefined;
  const hidden = collapsed?.result === result ? collapsed?.uris : undefined;
  const expandDirectory = (uri: string) => {
    if (keyword) {
      const uris = new Set(hidden);
      if (!uris.delete(uri)) uris.add(uri);
      setCollapsed({ result, uris });
    } else {
      const row = tree.rows.find(
        (item) => item.kind === 'entry' && item.uri === uri,
      );
      if (row?.kind === 'entry') return tree.toggleExpand(row);
    }
  };
  const references: VikingBotReferences = {
    rows: keyword
      ? (result?.rows ?? [])
          .filter(
            (row) =>
              !hidden ||
              ![...hidden].some((uri) => row.uri.startsWith(`${uri}/`)),
          )
          .map((row) => ({
            ...row,
            expanded: row.hasChildren ? !hidden?.has(row.uri) : undefined,
          }))
      : tree.rows,
    selected:
      selection?.selected ??
      (selected?.scope === scope ? selected.items : emptyReferences),
    onChange: onSelectionChange ?? onChange,
    onOpenChange,
    onQueryChange: setQuery,
    onExpand: (uri) => void expandDirectory(uri),
    onLoadMore: (uri) => tree.loadMore(uri),
    onRetry: (uri) => {
      if (!keyword) return tree.retry(uri ?? MENTION_ROOT);
      setSearch(undefined);
      setAttempt((current) => current + 1);
    },
    isLoading: keyword ? !result : !tree.isReady && tree.isInitializing,
    error: keyword ? result?.error : tree.error?.message,
  };
  return { references, clearReferences, expandDirectory };
}
