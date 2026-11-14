import {
  beginWorkflow,
  workflowsForData,
  type Workflow,
} from '../../shared/monitoring/workflow.js';
import { tagModel } from '../../shared/monitoring/data.js';
import { bindingOf } from '../../shared/monitoring/client.js';
import { useWorkflowCommit } from '../../shared/monitoring/use-workflow-commit.js';
import { QueryObserver } from '@tanstack/query-core';
import { useMemo, useRef, useSyncExternalStore } from 'react';
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
type SearchTarget =
  | 'file_browser_search_find'
  | 'file_browser_search_query'
  | 'file_browser_search_grep'
  | 'file_browser_search_glob';
export interface SearchState {
  monitoring?: Workflow<SearchTarget>;
  result: SearchResult | null;
  loading: boolean;
  error: Error | null;
  retry: () => void;
}
export interface UseFsSearchOptions {
  client: OvClient | null;
  mode: SearchMode;
  /** Already debounced by the calling block. */
  query: string;
  filters: SearchFilters;
  rootUri: string;
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
  requestOptions,
}: UseFsSearchOptions): SearchState {
  const analytics = useAnalyticsRef();
  const retryCause = useRef<WeakRef<Error> | undefined>(undefined);
  const {
    request,
    error,
    aborted,
    retry: retryScope,
  } = useQueryScope(
    client,
    requestOptions,
    `file_browser_search_${mode === 'search' ? 'query' : mode}` as SearchTarget,
  );
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
              const retryError = retryCause.current?.deref();
              retryCause.current = undefined;
              const monitoring = beginWorkflow(
                current.client,
                `file_browser_search_${mode === 'search' ? 'query' : mode}` as SearchTarget,
                signal,
                { retryError, trigger: retryError ? 'retry' : undefined },
              );
              const operation = analytics.current && startOperation();
              const params = () => ({
                operation_id: operation!.operation_id,
                mode,
                has_filters: hasFilters,
              });
              if (operation)
                analytics.current?.emit('file_browser_search_submit', params);
              try {
                const raw = await (
                  monitoring?.client(current.client) ?? current.client
                ).request({
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
                tagModel(bindingOf(current.client), result, monitoring?.span);
                return result;
              } catch (cause) {
                monitoring?.fail(cause, 'protocol', signal);
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
  const result = useSyncExternalStore(subscribe, getSnapshot);
  const monitoring = workflowsForData(
    request?.client ?? null,
    [result?.data],
    `file_browser_search_${mode === 'search' ? 'query' : mode}` as SearchTarget,
  )[0];
  useWorkflowCommit(
    monitoring,
    !!result?.data && !result.isFetching && !result.error,
    () => ({ resultCount: result!.data!.total, hasMore: null }),
  );
  return {
    monitoring,
    result: result?.data ?? null,
    loading:
      !!client && !!query.trim() && !error && (!result || result.isFetching),
    error: error ?? result?.error ?? null,
    retry: () => {
      if (aborted) return;
      if (observer) {
        const binding = bindingOf(client);
        retryCause.current =
          result?.error && binding?.controller.options && binding.active()
            ? new WeakRef(result.error)
            : undefined;
        void observer.refetch({ cancelRefetch: false });
      } else retryScope();
    },
  };
}
