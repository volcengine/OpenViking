import { useCallback } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { FileBrowserExperienceDataSource } from './experience-data-source.js';
import { experienceDateQuery } from './experience-data.js';
import { useDashboardQuery } from './use-dashboard-query.js';

export function useExperienceEffect({
  uri,
  days,
  loader,
  requestOptions,
  enabled,
  refreshGeneration,
}: {
  uri: string;
  days: number;
  loader?: FileBrowserExperienceDataSource['effect'];
  requestOptions?: OvRequestOptions;
  enabled: boolean;
  refreshGeneration?: number;
}) {
  const loadEffect = useCallback(
    async (_client: OvClient, { signal }: { signal: AbortSignal }) =>
      loader?.({ uri, ...experienceDateQuery(days), signal }),
    [uri, days, loader],
  );
  return useDashboardQuery(
    loadEffect,
    requestOptions,
    enabled && !!loader,
    refreshGeneration,
    'file_browser_experience_dashboard_load',
    undefined,
    true,
  );
}
