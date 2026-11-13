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
} from './experience-data.js';
import type { ExperienceOutcomeResult } from './experience-data.js';

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
  const outcomes = useDashboardQuery(
    loadOutcomes,
    requestOptions,
    enabled && !directory && tab === 'impact',
    refreshGeneration,
  );
  return {
    list,
    sources,
    outcomes,
    page,
    setPage,
    days,
    setDays,
    tab,
    setTab,
  };
}
