import { useCallback } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { readExperienceSources } from './experience-data.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import { useDashboardQuery } from './use-dashboard-query.js';

export function useExperienceSources({
  uri,
  dataSource,
  requestOptions,
  enabled,
  refreshGeneration,
}: {
  uri: string;
  dataSource?: FileBrowserExperienceDataSource;
  requestOptions?: OvRequestOptions;
  enabled: boolean;
  refreshGeneration: number;
}) {
  const { sources: sourcesSource, sourceGroups: sourceGroupsSource } =
    dataSource ?? {};
  const loadSources = useCallback(
    async (
      client: OvClient,
      options: OvRequestOptions & { signal: AbortSignal },
    ) => {
      const request = { uri, signal: options.signal };
      return sourceGroupsSource
        ? { groups: await sourceGroupsSource(request) }
        : {
            items: await (sourcesSource
              ? sourcesSource(request)
              : readExperienceSources(client, uri, options)),
          };
    },
    [uri, sourcesSource, sourceGroupsSource],
  );
  return useDashboardQuery(
    loadSources,
    requestOptions,
    enabled,
    refreshGeneration,
    'file_browser_experience_dashboard_load',
    (value) => (value.groups ?? value.items)?.length === 0,
    !!(sourcesSource || sourceGroupsSource),
  );
}
