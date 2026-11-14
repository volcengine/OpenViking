import { beginWorkflow } from '../../shared/monitoring/workflow.js';
import { useWorkflowCommit } from '../../shared/monitoring/use-workflow-commit.js';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import {
  experienceDateQuery,
  EXPERIENCE_PAGE_SIZE,
} from './experience-data.js';
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
  dataSource,
  refreshGeneration = 0,
}: {
  uri: string;
  days: number;
  enabled: boolean;
  requestOptions?: OvRequestOptions;
  dataSource?: FileBrowserExperienceDataSource;
  refreshGeneration?: number;
}) {
  const client = useOvClient();
  const loadPage = dataSource?.trajectories;
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
      loadPage,
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
      loadPage,
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
  // 当前在途请求的控制器；身份变化、禁用与卸载都要 abort 它。用 ref 是因为
  // 它不属于渲染输出——放 state 会让每次请求都触发一次重渲染，也会让下面的
  // effect 因依赖变化反复重启。
  //
  // The controller of the request in flight; identity changes, disabling and unmount
  // abort it. A ref, because it is not render output: state would re-render on every
  // request and restart the effect below on each dependency change.
  const inFlight = useRef<AbortController | null>(null);
  const current = state.owner === owner;

  const load = useCallback(
    async (page: number, mode: 'first' | 'more') => {
      // 同一时刻只保留一个在途请求：新的一次先把上一个真正中止，而不只是让序号
      // 失效——否则旧连接会一直挂着，直到服务端返回。
      //
      // Only one request is in flight at a time: a new one aborts the previous for
      // real instead of merely invalidating its ticket, or the old connection would
      // hang until the server answers.
      inFlight.current?.abort();
      const ticket = ++requestId.current;
      setState((previous) => beginTrajectoryPage(previous, owner, mode));
      const controller = new AbortController();
      inFlight.current = controller;
      const signals = [controller.signal, signal, inheritedSignal].filter(
        (value): value is AbortSignal => Boolean(value),
      );
      const options = {
        baseURL,
        apiKey,
        accountId,
        userId,
        resourceId,
        signal: AbortSignal.any(signals),
      };
      const monitoring = beginWorkflow(
        client,
        'file_browser_experience_trajectory_load',
        options.signal,
      );
      if (loadPage) monitoring?.span.gap('opaque-host-service');
      try {
        const result = loadPage
          ? await loadPage({
              uri,
              offset: page * EXPERIENCE_PAGE_SIZE,
              limit: EXPERIENCE_PAGE_SIZE,
              ...experienceDateQuery(days),
              signal: options.signal,
            }).then(({ items, total, hasMore }) => ({
              items,
              total,
              has_more: hasMore,
            }))
          : await readExperienceTrajectoryPage(
              monitoring?.client(client!) ?? client!,
              uri,
              days,
              page,
              options,
            );
        if (options.signal?.aborted || ticket !== requestId.current) return;
        setState((previous) => ({
          ...appendTrajectoryPage(previous, owner, result, mode),
          monitoring,
          pageItemCount: result.items.length,
        }));
      } catch (cause) {
        if (options.signal?.aborted || ticket !== requestId.current) return;
        monitoring?.fail(cause, 'protocol', options.signal);
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
      loadPage,
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
    if (!enabled || !client) return undefined;
    // 本次 effect 期间在途的请求；清理时中止它，迟到响应不再传输也不再写回。
    // 序号失效由 `load` 在下次开始时递增完成，这里只负责中止网络请求。
    //
    // The request in flight during this effect; the cleanup aborts it so a late
    // response neither streams nor writes back. Ticket invalidation happens when the
    // next `load` starts; this only stops the network request.
    void load(0, 'first');
    return () => {
      // 清理时读取 ref 的最新值：这里要中止的是「此刻在途的那个请求」，而不是
      // effect 建立时那一个——加载更多会替换它，而加载更多同样属于本次挂载。
      // exhaustive-deps 针对的是把 ref 当渲染值用的场景，此处不是。
      //
      // Read the ref's latest value on cleanup: what must be aborted is the request
      // in flight right now, not the one present when the effect was set up —
      // load-more replaces it and belongs to the same mount. The lint rule targets
      // refs used as render values, which this is not.
      //
      // eslint-disable-next-line react-hooks/exhaustive-deps
      inFlight.current?.abort();
    };
  }, [client, enabled, load, owner]);

  useWorkflowCommit(
    state.monitoring,
    current && !state.loading && !state.loadingMore && !state.error,
    () => ({ itemCount: state.pageItemCount ?? 0 }),
  );

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
    monitoring: current ? state.monitoring : undefined,
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
