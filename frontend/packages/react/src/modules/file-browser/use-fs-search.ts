import { createSearchObserver, type SearchTarget } from './search-observer.js';
import { QueryObservation } from './query-observation.js';
import { type Workflow } from '../../shared/monitoring/workflow.js';
import { bindingOf } from '../../shared/monitoring/client.js';
import { useWorkflowCommit } from '../../shared/monitoring/use-workflow-commit.js';
import { useMemo, useRef, useSyncExternalStore } from 'react';
import { useAnalyticsRef } from '../../shared/analytics/context.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { useQueryScope } from './use-query-scope.js';
import type {
  SearchFilters,
  SearchMode,
  SearchResult,
} from './search-types.js';
import { buildSearchBody, isSemantic } from './search-model.js';

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
      createSearchObserver(
        request,
        mode,
        query,
        bodyKey,
        hasFilters,
        analytics,
        retryCause,
      ),
    [request, mode, query, bodyKey, hasFilters, analytics],
  );
  const observation = useMemo(
    () =>
      new QueryObservation(
        request?.client ?? null,
        `file_browser_search_${mode === 'search' ? 'query' : mode}` as SearchTarget,
      ),
    [request, mode],
  );
  const subscribe = useMemo(
    () =>
      observer && !aborted
        ? (notify: () => void) => {
            observation.observe(
              observer.getCurrentResult(),
              observer.getCurrentQuery(),
            );
            const unsubscribe = observer.subscribe((result) => {
              observation.observe(result, observer.getCurrentQuery());
              notify();
            });
            return () => {
              observation.cancel();
              unsubscribe();
            };
          }
        : emptySubscribe,
    [observer, aborted, observation],
  );
  const getSnapshot = useMemo(
    () => (observer ? () => observer.getCurrentResult() : emptySnapshot),
    [observer],
  );
  const result = useSyncExternalStore(subscribe, getSnapshot);
  const monitoring = observer
    ? observation.operations(observer.getCurrentQuery())[0]
    : undefined;
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
