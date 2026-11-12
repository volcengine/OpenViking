import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import type { OvRequestOptions } from '../../client/types.js';
import { readExperienceTrajectoryPage } from './experience-trajectory-page.js';
import {
  appendTrajectoryPage,
  beginTrajectoryPage,
  emptyTrajectoryPages,
  failTrajectoryPage,
} from './experience-trajectory-pages.js';
import type { TrajectoryPages } from './experience-trajectory-pages.js';

/**
 * 经验轨迹的「加载更多」数据层：已加载的页留在本 Hook 里，加载更多只追加下一页，
 * 因此等待与失败都不会清空已有记录，请求量随页数线性增长。资源、账号或时间范围
 * 变化时整组重置。不复用 `useDashboardQuery`，因为它在身份变化时隐藏旧数据。
 *
 * The load-more layer for trajectories: loaded pages stay in this Hook, "load more"
 * only appends the next page, so waiting or failing never clears existing rows and
 * the request count grows linearly. A resource, account or date-range change resets
 * everything. `useDashboardQuery` is not reused because it hides stale data.
 */
export function useExperienceTrajectories({
  uri,
  days,
  enabled,
  requestOptions,
  refreshGeneration = 0,
}: {
  uri: string;
  days: number;
  enabled: boolean;
  requestOptions?: OvRequestOptions;
  refreshGeneration?: number;
}) {
  const client = useOvClient();
  const inheritedSignal = useOvSignal();
  const { baseURL, apiKey, accountId, userId, resourceId, signal } =
    requestOptions ?? {};
  // 身份决定「哪一组页」：其中任何一项变化都要丢弃已加载的页。
  //
  // The identity decides which set of pages this is; a change discards what is loaded.
  const owner = useMemo(
    () => [
      uri,
      days,
      client,
      baseURL,
      apiKey,
      accountId,
      userId,
      resourceId,
      signal,
      inheritedSignal,
      refreshGeneration,
    ],
    [
      uri,
      days,
      client,
      baseURL,
      apiKey,
      accountId,
      userId,
      resourceId,
      signal,
      inheritedSignal,
      refreshGeneration,
    ],
  );
  const [state, setState] = useState<TrajectoryPages>(() =>
    emptyTrajectoryPages(owner),
  );
  // 请求序号：身份变化或再次触发后，迟到响应不得写回。
  //
  // A request counter so a late response cannot write after the identity changed.
  const requestId = useRef(0);
  const current = state.owner === owner;

  const load = useCallback(
    async (page: number, mode: 'first' | 'more') => {
      const ticket = ++requestId.current;
      setState((previous) => beginTrajectoryPage(previous, owner, mode));
      const controller = new AbortController();
      const signals = [controller.signal, signal, inheritedSignal].filter(
        (value): value is AbortSignal => Boolean(value),
      );
      const options: OvRequestOptions = {
        baseURL,
        apiKey,
        accountId,
        userId,
        resourceId,
        signal: AbortSignal.any(signals),
      };
      try {
        const result = await readExperienceTrajectoryPage(
          client!,
          uri,
          days,
          page,
          options,
        );
        if (options.signal?.aborted || ticket !== requestId.current) return;
        setState((previous) =>
          appendTrajectoryPage(previous, owner, result, mode),
        );
      } catch (cause) {
        if (options.signal?.aborted || ticket !== requestId.current) return;
        setState((previous) => failTrajectoryPage(previous, owner, cause));
      }
    },
    [
      accountId,
      apiKey,
      baseURL,
      client,
      days,
      inheritedSignal,
      owner,
      resourceId,
      signal,
      uri,
      userId,
    ],
  );

  // useEffect 必要性：首屏随查询身份同步远端数据，不能由点击或渲染计算触发；
  // 身份变化、卸载或刷新时让在途请求失效，`requestId` 因此不再写回。
  //
  // Why an effect: the first page follows the query identity to stay in sync with the
  // server, which neither a click nor render can do; identity changes, unmount and
  // refreshes invalidate the request in flight so `requestId` never writes back.
  useEffect(() => {
    if (!enabled || !client) return;
    // 清理交给 `load` 自己的 abort 信号与序号：身份一变，旧请求的 signal 就被中止，
    // 序号也不再匹配，迟到响应不会写回。这里不需要额外的清理动作。
    //
    // Cleanup is handled by `load`'s own abort signal and ticket: a new identity
    // aborts the old request and its ticket no longer matches, so a late response
    // never writes back. Nothing extra is needed here.
    void load(0, 'first');
    return undefined;
  }, [client, enabled, load, owner]);

  const loadMore = useCallback(() => {
    if (state.loadingMore || !state.hasMore) return;
    // 页数由 `loadedPages` 决定，不从条数反推——服务端返回的条数与页大小未必一致。
    //
    // The page index comes from `loadedPages`, never from the row count: the server
    // need not return exactly one page's worth of rows.
    void load(state.loadedPages, 'more');
  }, [load, state.hasMore, state.loadedPages, state.loadingMore]);

  const retry = useCallback(() => {
    // 失败后重试当前要补的那一页；首批失败则从第一页重来。
    //
    // Retry the page that was being appended, or start over after a failed first page.
    void load(
      state.loadedPages === 0 ? 0 : state.loadedPages,
      state.loadedPages === 0 ? 'first' : 'more',
    );
  }, [load, state.loadedPages]);

  return {
    data: current
      ? {
          items: state.items,
          total: state.total,
          has_more: state.hasMore,
        }
      : undefined,
    loading: enabled && (!current || state.loading),
    loadingMore: current && state.loadingMore,
    error: current ? state.error : null,
    hasMore: current ? state.hasMore : false,
    loadMore,
    retry,
  };
}
