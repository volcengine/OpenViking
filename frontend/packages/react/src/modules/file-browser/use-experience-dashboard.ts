import { useCallback, useMemo, useState } from 'react';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { OV_PATHS } from '../../client/paths.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { toApiDate } from '../../shared/primitives/date-picker/api-date.js';
import { experienceDateBounds } from './experience-date-range.js';
import {
  isExperienceDirectory,
  readExperiences,
  readExperienceSources,
  EXPERIENCE_PAGE_SIZE,
} from './experience-data.js';
import type {
  ExperienceTrajectoryPage,
  ExperienceOutcomeResult,
} from './experience-data.js';

/**
 * 把天数换成接口的日期查询参数。
 *
 * 边界按 UTC 取：`agent_evolution` 的列表接口收 UTC `YYYY-MM-DD`，服务端用
 * `timezone.utc` 拼边界，东八区的本地零点在 UTC 是前一天。
 *
 * Turns a day count into the endpoint's date query parameters.
 *
 * Boundaries are UTC: the `agent_evolution` list endpoints take UTC `YYYY-MM-DD` and the
 * server builds its bounds with `timezone.utc`; a local midnight in UTC+8 is the previous
 * day in UTC.
 */
function dateQuery(days: number) {
  const bounds = experienceDateBounds(days);
  if (!bounds) return {};
  return {
    start_date: toApiDate(bounds.start, 'utc'),
    end_date: toApiDate(bounds.end, 'utc'),
  };
}

export function useExperienceDashboard({
  uri,
  requestOptions,
  enabled = true,
  refreshGeneration = 0,
}: {
  uri: string;
  requestOptions?: OvRequestOptions;
  enabled?: boolean;
  refreshGeneration?: number;
}) {
  const client = useOvClient();
  const signal = useOvSignal();
  const owner = useMemo(
    () => [
      uri,
      client,
      signal,
      requestOptions?.baseURL,
      requestOptions?.apiKey,
      requestOptions?.accountId,
      requestOptions?.userId,
      requestOptions?.resourceId,
    ],
    [
      uri,
      client,
      signal,
      requestOptions?.baseURL,
      requestOptions?.apiKey,
      requestOptions?.accountId,
      requestOptions?.userId,
      requestOptions?.resourceId,
    ],
  );
  const [state, setState] = useState({
    owner,
    page: 0,
    days: 7,
    tab: 'impact' as 'impact' | 'source' | 'test',
  });
  const current =
    state.owner === owner
      ? state
      : { owner, page: 0, days: 7, tab: 'impact' as const };
  const { page, days, tab } = current;
  const setPage = (value: number) =>
    setState({ ...current, page: Math.max(0, value) });
  const setDays = (value: number) =>
    setState({ ...current, days: value, page: 0 });
  const setTab = (value: 'impact' | 'source' | 'test') =>
    setState({ ...current, tab: value, page: 0 });
  const directory = isExperienceDirectory(uri);
  const loadList = useCallback(
    (client: OvClient, options: OvRequestOptions) =>
      readExperiences(client, uri, page, options),
    [uri, page],
  );
  const loadSources = useCallback(
    (client: OvClient, options: OvRequestOptions) =>
      readExperienceSources(client, uri, options),
    [uri],
  );
  // 轨迹是「向下滚动加载更多」而不是翻页：每次只取一页，再与已加载的页拼接。
  //
  // 拼接放在取数回调里，是因为 `useDashboardQuery` 在查询身份变化时会隐藏旧数据
  // ——身份里包含 `page`，翻页时旧结果立刻不可见。回调每次从第一页开始重新取、
  // 取到目标页为止，拼接后的结果才是完整列表；资源、账号或时间范围变化时
  // `page` 已被 `setDays`/`setTab` 重置为 0，天然回到第一页。
  //
  // Trajectories load by "scroll for more" rather than by page: each request takes
  // one page, and the pages already loaded are concatenated.
  //
  // The concatenation lives in the loader because `useDashboardQuery` hides stale
  // data when its identity changes — and the identity includes `page`, so the
  // previous page would vanish on every step. The loader always starts from the
  // first page and walks up to the requested one, which yields the whole list;
  // changing the resource, account or date range resets `page` to 0 through
  // `setDays`/`setTab`, so those naturally return to the first page.
  const loadTrajectories = useCallback(
    async (client: OvClient, options: OvRequestOptions) => {
      const pages: ExperienceTrajectoryPage['items'] = [];
      let total = 0;
      let hasMore = false;
      for (let index = 0; index <= page; index++) {
        const result = await client.request<ExperienceTrajectoryPage>({
          method: 'GET',
          path: OV_PATHS.experienceTrajectories,
          query: {
            experience_uri: uri,
            offset: index * EXPERIENCE_PAGE_SIZE,
            limit: EXPERIENCE_PAGE_SIZE,
            ...dateQuery(days),
          },
          options,
        });
        if (
          !Array.isArray(result?.items) ||
          !Number.isSafeInteger(result.total) ||
          result.total < 0 ||
          result.items.some(
            (item) =>
              typeof item?.uri !== 'string' || typeof item.name !== 'string',
          )
        )
          throw new TypeError('Invalid Experience trajectories');
        pages.push(...result.items);
        total = result.total;
        hasMore = result.has_more;
        // 后面的页已经没有数据时不必再请求。
        //
        // Stop once a page comes back short of a full one.
        if (!result.has_more) break;
      }
      return { items: pages, total, has_more: hasMore };
    },
    [uri, page, days],
  );
  const loadOutcomes = useCallback(
    async (client: OvClient, options: OvRequestOptions) => {
      const result = await client.request<ExperienceOutcomeResult>({
        method: 'GET',
        path: OV_PATHS.experienceOutcomes,
        query: { experience_uri: uri, ...dateQuery(days) },
        options,
      });
      if (
        !Array.isArray(result?.outcome_distribution) ||
        result.outcome_distribution.some(
          (entry) =>
            ![
              'success',
              'partial',
              'unfinished',
              'failure',
              'unknown',
            ].includes(entry?.outcome) ||
            !Number.isSafeInteger(entry.count) ||
            entry.count < 0,
        )
      )
        throw new TypeError('Invalid Experience outcomes');
      return result.outcome_distribution;
    },
    [uri, days],
  );
  const list = useDashboardQuery(
    loadList,
    requestOptions,
    enabled && directory,
    refreshGeneration,
  );
  const sources = useDashboardQuery(
    loadSources,
    requestOptions,
    enabled && !directory && tab === 'source',
    refreshGeneration,
  );
  const trajectories = useDashboardQuery(
    loadTrajectories,
    requestOptions,
    enabled && !directory && tab === 'impact',
    refreshGeneration,
  );
  const outcomes = useDashboardQuery(
    loadOutcomes,
    requestOptions,
    enabled && !directory && tab === 'impact',
    refreshGeneration,
  );
  return {
    list,
    sources,
    trajectories,
    outcomes,
    page,
    setPage,
    days,
    setDays,
    tab,
    setTab,
  };
}
