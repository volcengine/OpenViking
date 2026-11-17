import { QueriesObserver } from '@tanstack/query-core';
import { useCallback, useEffect, useMemo, useSyncExternalStore } from 'react';
import { isOvClientError } from '../../client/errors.js';
import { OV_PATHS } from '../../client/paths.js';
import { useOvClient } from '../../client/provider.js';
import { queryKeys } from '../../client/query-keys.js';
import type { OvRequestOptions } from '../../client/types.js';
import { useQueryScope } from '../../shared/request/use-query-scope.js';
import {
  InvalidWikiConflictSearchResponse,
  readWikiConflictSummary,
  type WikiConflictSummary,
} from './wiki-conflict-summary.js';

const emptyResults = [] as const;
const emptySnapshot = () => emptyResults;

function needsConflictListing(error: unknown) {
  return (
    error instanceof InvalidWikiConflictSearchResponse ||
    (isOvClientError(error) &&
      (error.statusCode === 404 ||
        error.statusCode === 405 ||
        error.statusCode === 501))
  );
}

export function useWikiConflictSummaries(
  directories: readonly string[],
  generation: number,
  requestOptions?: OvRequestOptions,
) {
  const client = useOvClient('local');
  const { request, aborted } = useQueryScope(
    directories.length ? client : null,
    requestOptions,
  );
  const queryClient = request?.queryClient;
  const directoriesKey = JSON.stringify([...new Set(directories)].sort());
  const options = useMemo(() => {
    if (!request || aborted) return [];
    return (JSON.parse(directoriesKey) as string[]).map((uri) => ({
      uri,
      queryKey: [
        ...queryKeys.search(request.scope, OV_PATHS.searchFind, {
          wikiConflictDirectory: uri,
        }),
        generation,
      ],
      queryFn: async ({ signal }: { signal: AbortSignal }) => {
        const current = await request.current(signal);
        return readWikiConflictSummary(current.client, uri, {
          ...current.options,
          signal,
        });
      },
      staleTime: Infinity,
      retry: false,
    }));
  }, [request, aborted, directoriesKey, generation]);
  const observer = useMemo(
    () => (queryClient ? new QueriesObserver(queryClient, []) : undefined),
    [queryClient],
  );
  const subscribe = useCallback(
    (listener: () => void) => observer?.subscribe(listener) ?? (() => {}),
    [observer],
  );
  useSyncExternalStore(
    subscribe,
    () => observer?.getCurrentResult() ?? emptyResults,
    emptySnapshot,
  );
  // Synchronize the subscribed search queries when the visible directory list
  // changes. Retained observers keep their requests, and removed ones unsubscribe.
  useEffect(() => {
    observer?.setQueries(options);
  }, [observer, options]);

  const summaries = new Map<string, WikiConflictSummary>();
  const listingRoots: string[] = [];
  for (const { uri, queryKey } of options) {
    const state = queryClient?.getQueryState<WikiConflictSummary>(queryKey);
    if (state?.status === 'success' && state.data)
      summaries.set(uri, state.data);
    if (state?.data?.limited || needsConflictListing(state?.error))
      listingRoots.push(uri);
  }
  return { summaries, listingRoots };
}
