import { useCallback, useEffect, useMemo, useState } from 'react';
import { createOvClient } from '../../client/client.js';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { VikingBotReferences } from '../../components/viking-bot/index.js';
import type { ConsoleChatOptions } from './types.js';
import { useDirectoryTree } from '../../shared/directory-tree/use-directory-tree.js';
import { mentionSearchRows, MENTION_ROOT } from './mention-search.js';
import {
  referenceSearchInput,
  searchReferenceMatches,
} from './references/search.js';
import { useReferenceSelection } from './references/use-reference-selection.js';

export function useConversationReferences({
  enabled,
  requestOptions,
  scopeKey,
  selection,
  directoryClient,
  searchSkills = false,
}: {
  enabled: boolean;
  requestOptions?: OvRequestOptions;
  scopeKey?: string;
  selection?: ConsoleChatOptions['referenceSelection'];
  directoryClient?: OvClient;
  searchSkills?: boolean;
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
  const { selected, onChange, clearReferences, prepareReferenceClear } =
    useReferenceSelection(scope, enabled ? selection : undefined);
  const [openCount, setOpenCount] = useState(0);
  const onOpenChange = useCallback((open: boolean) => {
    setOpenCount((count) => (open ? count + 1 : Math.max(0, count - 1)));
  }, []);
  const [query, setQuery] = useState('');
  const { keyword, searchKey } = referenceSearchInput(query, searchSkills);
  const opened = enabled && openCount > 0;
  const tree = useDirectoryTree({
    client: opened ? client : null,
    rootUri: MENTION_ROOT,
    requestOptions: scope.options,
    cacheScopeKey: scopeKey,
  });
  const [search, setSearch] = useState<{
    scope: typeof scope;
    key: string;
    rows?: ReturnType<typeof mentionSearchRows>;
    error?: string;
  }>();
  const [attempt, setAttempt] = useState(0);
  const [collapsed, setCollapsed] = useState<{
    result: typeof search;
    uris: Set<string>;
  }>();
  // Syncs the open picker and query with remote glob results after a debounce.
  // Picker and scope changes can happen outside input events, so an Effect owns the request.
  // Query/scope changes or unmount clear the timer and abort the old request.
  useEffect(() => {
    if (!opened || !searchKey) return;
    const controller = new AbortController();
    const requestSignal = signal
      ? AbortSignal.any([signal, controller.signal])
      : controller.signal;
    const timer = setTimeout(() => {
      void searchReferenceMatches(scope, searchKey, requestSignal, searchSkills)
        .then((results) => {
          if (requestSignal.aborted) return;
          const matches = results.flat();
          setSearch({
            scope,
            key: searchKey,
            rows: mentionSearchRows(matches),
          });
        })
        .catch((error: unknown) => {
          if (requestSignal.aborted) return;
          setSearch({
            scope,
            key: searchKey,
            error: error instanceof Error ? error.message : String(error),
          });
        });
    }, 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [opened, searchKey, scope, signal, attempt, searchSkills]);
  if (!enabled)
    return { references: undefined, clearReferences, prepareReferenceClear };

  const result =
    search?.scope === scope && search.key === searchKey ? search : undefined;
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
    selected,
    onChange,
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
  return {
    references,
    clearReferences,
    prepareReferenceClear,
    expandDirectory,
  };
}
