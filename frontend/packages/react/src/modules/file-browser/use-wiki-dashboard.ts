import { useCallback } from 'react';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { parseWikiFileConflicts } from './wiki-conflicts.js';
import { readFileContent } from './fs-read-content.js';
import { readDashboardStat } from './dashboard-resource.js';
import { readWikiConflictSummary } from './wiki-conflict-summary.js';
import type {
  WikiDirectoryData,
  WikiFileData,
} from '../../components/wiki-dashboard/types.js';

type WikiDashboardResult =
  | { kind: 'directory'; data: WikiDirectoryData }
  | { kind: 'file'; data: WikiFileData };

export async function readWikiDashboard(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
): Promise<WikiDashboardResult> {
  const stat = await readDashboardStat(client, uri, options);
  uri = stat.uri;
  if (!stat.isDir) {
    const result = await readFileContent(client, uri, options, -1, true);
    const parsed = parseWikiFileConflicts(result.wikiSource ?? result.content);
    if (parsed.status === 'invalid')
      throw new TypeError('Invalid CONFLICTS_CONTENT metadata');
    return {
      kind: 'file',
      data: {
        path: uri,
        conflicts: parsed.status === 'available' ? parsed.records : [],
      },
    };
  }
  return {
    kind: 'directory',
    data: await readWikiConflictSummary(client, uri, options),
  };
}

export function useWikiDashboard({
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
  const load = useCallback(
    (client: OvClient, options: OvRequestOptions) =>
      readWikiDashboard(client, uri, options),
    [uri],
  );
  return useDashboardQuery(
    load,
    requestOptions,
    enabled,
    refreshGeneration,
    'file_browser_wiki_dashboard_load',
    (value) =>
      value.kind === 'directory'
        ? value.data.files.length === 0
        : value.data.conflicts.length === 0,
  );
}
