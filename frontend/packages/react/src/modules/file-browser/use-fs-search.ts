import { QueryObserver } from '@tanstack/query-core';
import { useEffect, useMemo, useRef, useSyncExternalStore } from 'react';
import { useAnalyticsRef } from '../../shared/analytics/context.js';
import { startOperation, errorKind } from '../../shared/analytics/reporter.js';
import { OV_PATHS, type OvPath } from '../../client/paths.js';
import { queryKeys } from '../../client/query-keys.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { useQueryScope } from './use-query-scope.js';
import type {
  SearchFilters,
  SearchMode,
  SearchResult,
} from './search-types.js';
import {
  buildSearchBody,
  isSemantic,
  normalizeFindResult,
  normalizeGlobResult,
  normalizeGrepResult,
  type RawFindResult,
} from './search-model.js';

const SEARCH_PATHS: Record<SearchMode, OvPath> = {
  find: OV_PATHS.searchFind,
  search: OV_PATHS.searchQuery,
  grep: OV_PATHS.searchGrep,
  glob: OV_PATHS.searchGlob,
};
export interface SearchState {
  result: SearchResult | null;
  loading: boolean;
  error: Error | null;
}
export interface UseFsSearchOptions {
  client: OvClient | null;
  mode: SearchMode;
  /** Already debounced by the calling block. */
  query: string;
  filters: SearchFilters;
  rootUri: string;
  generation?: number;
  requestOptions?: OvRequestOptions;
}
function normalize(mode: SearchMode, raw: unknown): SearchResult {
  const payload = (raw ?? {}) as { matches?: unknown } & RawFindResult;
  return mode === 'grep'
    ? normalizeGrepResult(payload.matches)
    : mode === 'glob'
      ? normalizeGlobResult(payload.matches)
      : normalizeFindResult(payload);
}
const emptySubscribe = () => () => {};
const emptySnapshot = () => undefined;

export function useFsSearch({
  client,
  mode,
  query,
  filters,
  rootUri,
  generation = 0,
  requestOptions,
}: UseFsSearchOptions): SearchState {
  const analytics = useAnalyticsRef();
  const { request, error, aborted } = useQueryScope(client, requestOptions);
  const bodyKey = JSON.stringify(
    buildSearchBody(mode, query, filters, rootUri),
  );
  const hasFilters = Boolean(
    filters.scopes.length ||
    (isSemantic(mode) &&
      (filters.levels.length || filters.since || filters.until)) ||
    (mode === 'grep' && filters.caseInsensitive),
  );
  const observer = useMemo(
    () =>
      request && query.trim()
        ? new QueryObserver(request.queryClient, {
            queryKey: queryKeys.search(
              request.scope,
              SEARCH_PATHS[mode],
              JSON.parse(bodyKey),
            ),
            queryFn: async ({ signal }) => {
              const current = await request.current(signal);
              const operation = analytics.current && startOperation();
              const params = () => ({
                operation_id: operation!.operation_id,
                mode,
                has_filters: hasFilters,
              });
              if (operation)
                analytics.current?.emit('file_browser_search_submit', params);
              try {
                const raw = await current.client.request({
                  method: 'POST',
                  path: SEARCH_PATHS[mode],
                  body: JSON.parse(bodyKey),
                  options: { ...current.options, signal },
                });
                signal.throwIfAborted();
                const result = normalize(mode, raw);
                if (operation)
                  analytics.current?.emit(
                    'file_browser_search_success',
                    () => ({
                      ...params(),
                      ...operation.finish(),
                      result_count: result.total,
                    }),
                  );
                return result;
              } catch (cause) {
                if (!signal.aborted && operation)
                  analytics.current?.emit('file_browser_search_fail', () => ({
                    ...params(),
                    ...operation.finish(),
                    error_kind: errorKind(cause),
                  }));
                throw cause;
              }
            },
          })
        : undefined,
    [request, mode, bodyKey, query, hasFilters, analytics],
  );
  const subscribe = useMemo(
    () =>
      observer && !aborted
        ? (notify: () => void) => observer.subscribe(notify)
        : emptySubscribe,
    [observer, aborted],
  );
  const getSnapshot = useMemo(
    () => (observer ? () => observer.getCurrentResult() : emptySnapshot),
    [observer],
  );
  const result = useSyncExternalStore(subscribe, getSnapshot, emptySnapshot);
  const previousGeneration = useRef(generation);
  // A host refresh changes server data without changing search parameters.
  // Refetch the current shared query; superseded observers unsubscribe normally.
  useEffect(() => {
    if (previousGeneration.current !== generation) {
      previousGeneration.current = generation;
      if (!aborted) void observer?.refetch({ cancelRefetch: false });
    }
  }, [generation, observer, aborted]);
  return {
    result: result?.data ?? null,
    loading:
      !!client && !!query.trim() && !error && (!result || result.isFetching),
    error: error ?? result?.error ?? null,
  };
}
