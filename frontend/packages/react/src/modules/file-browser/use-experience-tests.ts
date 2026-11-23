import { useCallback } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import { EXPERIENCE_PAGE_SIZE } from './experience-data.js';
import { useDashboardQuery } from './use-dashboard-query.js';

export function useExperienceTests({
  uri,
  page,
  loader,
  requestOptions,
  enabled,
  refreshGeneration,
}: {
  uri: string;
  page: number;
  loader?: FileBrowserExperienceDataSource['tests'];
  requestOptions?: OvRequestOptions;
  enabled: boolean;
  refreshGeneration: number;
}) {
  const loadTests = useCallback(
    async (
      _client: OvClient,
      { signal }: OvRequestOptions & { signal: AbortSignal },
    ) =>
      loader?.({
        uri,
        offset: page * EXPERIENCE_PAGE_SIZE,
        limit: EXPERIENCE_PAGE_SIZE,
        signal,
      }),
    [uri, page, loader],
  );
  return useDashboardQuery(
    loadTests,
    requestOptions,
    enabled && !!loader,
    refreshGeneration,
    'file_browser_experience_dashboard_load',
    (value) => value?.items.length === 0,
    true,
  );
}
