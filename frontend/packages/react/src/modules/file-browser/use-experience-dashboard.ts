import { useCallback, useMemo, useState } from 'react';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { OV_PATHS } from '../../client/paths.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import type {
  ExperienceListPage,
  FileBrowserExperienceDataSource,
} from './experience-data-source.js';
import {
  experienceDateQuery,
  EXPERIENCE_PAGE_SIZE,
  isExperienceDirectory,
  readExperiences,
  readExperienceSources,
} from './experience-data.js';
import type { ExperienceOutcomeResult } from './experience-data.js';

export function useExperienceDashboard({
  uri,
  requestOptions,
  dataSource,
  enabled = true,
  refreshGeneration = 0,
}: {
  uri: string;
  requestOptions?: OvRequestOptions;
  dataSource?: FileBrowserExperienceDataSource;
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
  const {
    list: listSource,
    sources: sourcesSource,
    outcomes: outcomesSource,
  } = dataSource ?? {};
  const loadList = useCallback(
    async (
      client: OvClient,
      options: OvRequestOptions & { signal: AbortSignal },
    ): Promise<ExperienceListPage> => {
      const custom = await listSource?.({
        uri,
        offset: page * EXPERIENCE_PAGE_SIZE,
        limit: EXPERIENCE_PAGE_SIZE,
        signal: options.signal,
      });
      options.signal.throwIfAborted();
      return custom ?? readExperiences(client, uri, page, options);
    },
    [uri, page, listSource],
  );
  const loadSources = useCallback(
    async (
      client: OvClient,
      options: OvRequestOptions & { signal: AbortSignal },
    ) =>
      sourcesSource
        ? sourcesSource({ uri, signal: options.signal })
        : readExperienceSources(client, uri, options),
    [uri, sourcesSource],
  );
  const loadOutcomes = useCallback(
    async (
      client: OvClient,
      options: OvRequestOptions & { signal: AbortSignal },
    ) => {
      const { startDate, endDate } = experienceDateQuery(days);
      if (outcomesSource)
        return outcomesSource({
          uri,
          startDate,
          endDate,
          signal: options.signal,
        });
      const result = await client.request<ExperienceOutcomeResult>({
        method: 'GET',
        path: OV_PATHS.experienceOutcomes,
        query: {
          experience_uri: uri,
          start_date: startDate,
          end_date: endDate,
        },
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
    [uri, days, outcomesSource],
  );
  const list = useDashboardQuery(
    loadList,
    requestOptions,
    enabled && directory,
    refreshGeneration,
    'file_browser_experience_dashboard_load',
    (value) => value.items.length === 0,
    !!listSource,
  );
  const sources = useDashboardQuery(
    loadSources,
    requestOptions,
    enabled && !directory && tab === 'source',
    refreshGeneration,
    'file_browser_experience_dashboard_load',
    (value) => value.length === 0,
    !!sourcesSource,
  );
  const outcomes = useDashboardQuery(
    loadOutcomes,
    requestOptions,
    enabled && !directory && tab === 'impact',
    refreshGeneration,
    'file_browser_experience_dashboard_load',
    (value) => value.length === 0,
    !!outcomesSource,
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
