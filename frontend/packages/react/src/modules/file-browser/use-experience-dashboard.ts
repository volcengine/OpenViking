import { useCallback, useMemo, useState } from 'react';
import { useOvClient, useOvSignal } from '../../client/provider.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { OV_PATHS } from '../../client/paths.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import {
  experienceDateRange,
  isExperienceDirectory,
  readExperiences,
  readExperienceSources,
  EXPERIENCE_PAGE_SIZE,
} from './experience-data.js';
import type {
  ExperienceTrajectoryPage,
  ExperienceOutcomeResult,
} from './experience-data.js';

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
  const loadTrajectories = useCallback(
    async (client: OvClient, options: OvRequestOptions) => {
      const result = await client.request<ExperienceTrajectoryPage>({
        method: 'GET',
        path: OV_PATHS.experienceTrajectories,
        query: {
          experience_uri: uri,
          offset: page * EXPERIENCE_PAGE_SIZE,
          limit: EXPERIENCE_PAGE_SIZE,
          ...experienceDateRange(days),
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
      return result;
    },
    [uri, page, days],
  );
  const loadOutcomes = useCallback(
    async (client: OvClient, options: OvRequestOptions) => {
      const result = await client.request<ExperienceOutcomeResult>({
        method: 'GET',
        path: OV_PATHS.experienceOutcomes,
        query: { experience_uri: uri, ...experienceDateRange(days) },
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
