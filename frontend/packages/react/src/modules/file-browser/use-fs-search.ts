import { useEffect, useState } from 'react';

import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { mergeFindResult, type FindResult } from './find-result.js';
import { isAbort, toError, type FsEntry } from './fs-model.js';

/** 每类最多返回多少条。Cap per category. */
const LIMIT = 20;

export interface SearchState {
  /** 结果行；未搜索时为 `null`。Result entries, `null` before any search. */
  results: FsEntry[] | null;
  /** 搜索是否进行中。Whether a search is running. */
  loading: boolean;
  /** 搜索失败的原因。Why the search failed. */
  error: Error | null;
}

export interface UseFsSearchOptions {
  client: OvClient | null;
  /** 已去抖的查询词；空串表示没有搜索。The debounced query; empty means no search. */
  query: string;
  targetUri: string;
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

/**
 * 语义搜索。结果按 URI 去重后合并成一个列表——搜索视图只需要「跳到哪个资源」，
 * 类别分组对使用者没有意义。
 *
 * Semantic search. Results merge into one list deduplicated by URI: the search
 * view only needs "which resource to jump to", so the category split adds
 * nothing for the user.
 */
export function useFsSearch({
  client,
  query,
  targetUri,
  generation = 0,
  requestOptions,
}: UseFsSearchOptions): SearchState {
  const [results, setResults] = useState<FsEntry[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  // 查询词决定发什么请求。切换查询必须取消上一次，否则慢的旧响应会盖住新结果；
  // 清空查询时回到未搜索状态。
  useEffect(() => {
    if (!client || !query) {
      setResults(null);
      setLoading(false);
      setError(null);
      return;
    }
    const controller = new AbortController();
    setLoading(true);
    setError(null);

    void client
      .request<FindResult>({
        method: 'POST',
        path: OV_PATHS.searchFind,
        body: { query, target_uri: targetUri, limit: LIMIT },
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
        (result) => {
          if (controller.signal.aborted) return;
          setResults(mergeFindResult(result));
          setLoading(false);
        },
        (cause: unknown) => {
          if (isAbort(cause) || controller.signal.aborted) return;
          setResults([]);
          setError(toError(cause));
          setLoading(false);
        },
      );

    return () => controller.abort();
  }, [client, query, targetUri, generation, requestOptions]);

  return { results, loading, error };
}
