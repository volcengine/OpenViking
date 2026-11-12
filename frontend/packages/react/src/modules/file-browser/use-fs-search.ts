import { useEffect, useMemo, useState } from 'react';

import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { isAbort, toError } from './fs-model.js';
import type {
  SearchFilters,
  SearchMode,
  SearchResult,
} from './search-types.js';
import {
  buildSearchBody,
  normalizeFindResult,
  normalizeGlobResult,
  normalizeGrepResult,
  type RawFindResult,
} from './search-model.js';

/** 模式到接口路径。`find`/`search` 语义检索，`grep`/`glob` 模式匹配。 */
const SEARCH_PATHS: Record<SearchMode, string> = {
  find: OV_PATHS.searchFind,
  search: OV_PATHS.searchSearch,
  grep: OV_PATHS.searchGrep,
  glob: OV_PATHS.searchGlob,
};

export interface SearchState {
  /** 归一后的结果；未搜索时为 `null`。Normalised result, `null` before any search. */
  result: SearchResult | null;
  /** 搜索是否进行中。Whether a search is running. */
  loading: boolean;
  /** 搜索失败的原因。Why the search failed. */
  error: Error | null;
}

export interface UseFsSearchOptions {
  client: OvClient | null;
  /** 检索方式。Retrieval mode. */
  mode: SearchMode;
  /** 已去抖的查询词；空串表示没有搜索。The debounced query; empty means no search. */
  query: string;
  /** 高级筛选。Advanced filters. */
  filters: SearchFilters;
  /** 搜索的根，也是目录筛选的兜底。The search root, also the scope fallback. */
  rootUri: string;
  /**
   * 重新取数的代次。宿主调用刷新时递增，让同一查询词重新请求——外部数据变了
   * 而查询词没变，只靠依赖是看不出来的。
   *
   * Reload generation. A host increments it on refresh so the same query is
   * requested again: the data changed while the query did not, which dependencies
   * alone cannot express.
   */
  generation?: number;
  requestOptions?: OvRequestOptions;
}

/** 把任意响应交给对应的归一函数。Routes a response to its normaliser. */
function normalize(mode: SearchMode, raw: unknown): SearchResult {
  const payload = (raw ?? {}) as { matches?: unknown } & RawFindResult;
  if (mode === 'grep') return normalizeGrepResult(payload.matches);
  if (mode === 'glob') return normalizeGlobResult(payload.matches);
  return normalizeFindResult(payload);
}

/**
 * 四种检索方式的取数。模式、查询词或筛选变化都发新请求并取消上一次——旧响应
 * 回来时若已作废，写进去就会盖住当前结果。
 *
 * Loading for the four retrieval modes. A mode, query or filter change issues a new
 * request and cancels the previous one: publishing a stale response would overwrite
 * the current view.
 */
export function useFsSearch({
  client,
  mode,
  query,
  filters,
  rootUri,
  generation = 0,
  requestOptions,
}: UseFsSearchOptions): SearchState {
  const [result, setResult] = useState<SearchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // 请求体按筛选的具体字段缓存，而不是依赖 `filters` 对象本身：调用方可能每次
  // 渲染都新建一个等价对象，把它当依赖会让 effect 反复重跑并取消上一次请求。
  //
  // The request is memoised on the individual filter fields rather than the `filters`
  // object: a caller may build an equivalent object every render, and depending on it
  // would re-run the effect and cancel the previous request each time.
  const body = useMemo(
    () => buildSearchBody(mode, query, filters, rootUri),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      mode,
      query,
      rootUri,
      filters.scopes,
      filters.levels,
      filters.timeField,
      filters.since,
      filters.until,
      filters.caseInsensitive,
    ],
  );

  // 查询词、模式与筛选决定发什么请求。切换时必须取消上一次，否则慢的旧响应会
  // 盖住新结果；清空查询时回到未搜索状态。
  //
  // Query, mode and filters decide the request. A switch has to cancel the previous
  // one, or a slow old response overwrites the new result; an empty query returns to
  // the pre-search state.
  useEffect(() => {
    if (!client || !query.trim()) {
      setResult(null);
      setLoading(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    void client
      .request<unknown>({
        method: 'POST',
        path: SEARCH_PATHS[mode],
        body,
        options: { ...requestOptions, signal: controller.signal },
      })
      // 成功也要复查取消状态：自定义 fetch 可以在 signal 已中止之后照常返回，
      // 这时把结果写进去就会让上一个账号/上一次查询的数据盖住当前视图。失败同理，
      // 一个已作废的请求不该把错误留给新的查询。
      //
      // Successes are re-checked for cancellation too: a custom fetch can resolve
      // normally after its signal was aborted, and publishing that result would let the
      // previous account or the previous query overwrite the current view. Failures
      // likewise: a request that was discarded must not leave its error behind.
      .then(
        (raw) => {
          if (controller.signal.aborted) return;
          setResult(normalize(mode, raw));
          setLoading(false);
        },
        (cause: unknown) => {
          if (isAbort(cause) || controller.signal.aborted) return;
          setResult({ memories: [], resources: [], skills: [], total: 0 });
          setError(toError(cause));
          setLoading(false);
        },
      );

    return () => controller.abort();
  }, [client, mode, query, generation, requestOptions, body]);

  return { result, loading, error };
}
