import { QueryObserver } from '@tanstack/query-core';
import { beginWorkflow } from '../../shared/monitoring/workflow.js';
import { tagModel } from '../../shared/monitoring/data.js';
import { bindingOf } from '../../shared/monitoring/client.js';
import { startOperation, errorKind } from '../../shared/analytics/reporter.js';
import type { useAnalyticsRef } from '../../shared/analytics/context.js';
import { OV_PATHS, type OvPath } from '../../client/paths.js';
import { queryKeys } from '../../client/query-keys.js';
import type { QueryRequest } from './use-query-scope.js';
import type { SearchMode, SearchResult } from './search-types.js';
import {
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
export type SearchTarget =
  | 'file_browser_search_find'
  | 'file_browser_search_query'
  | 'file_browser_search_grep'
  | 'file_browser_search_glob';
function normalize(mode: SearchMode, raw: unknown): SearchResult {
  const payload = (raw ?? {}) as { matches?: unknown } & RawFindResult;
  return mode === 'grep'
    ? normalizeGrepResult(payload.matches)
    : mode === 'glob'
      ? normalizeGlobResult(payload.matches)
      : normalizeFindResult(payload);
}
export function createSearchObserver(
  request: QueryRequest | undefined,
  mode: SearchMode,
  query: string,
  bodyKey: string,
  hasFilters: boolean,
  analytics: ReturnType<typeof useAnalyticsRef>,
  retryCause: { current: WeakRef<Error> | undefined },
) {
  return request && query.trim()
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
              analytics.current?.emit('file_browser_search_success', () => ({
                ...params(),
                ...operation.finish(),
                result_count: result.total,
              }));
            tagModel(
              bindingOf(current.client),
              request.queryClient.getQueryCache().find({
                queryKey: queryKeys.search(
                  request.scope,
                  SEARCH_PATHS[mode],
                  JSON.parse(bodyKey),
                ),
                exact: true,
              }),
              monitoring?.span,
            );
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
    : undefined;
}
