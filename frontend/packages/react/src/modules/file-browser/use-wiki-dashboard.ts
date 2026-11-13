import { useCallback } from 'react';
import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { useDashboardQuery } from './use-dashboard-query.js';
import { parseWikiFileConflicts } from './wiki-conflicts.js';
import { readFileContent } from './fs-read-content.js';
import { readDashboardStat } from './dashboard-resource.js';
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
  const page = await client.requestEnvelope<{ uri: string; isDir?: boolean }[]>(
    {
      method: 'GET',
      path: OV_PATHS.fsLs,
      query: {
        uri,
        recursive: true,
        output: 'agent',
        tags: ['has_conflict=true'],
        limit: 100,
        sort_by: 'mtime',
        sort_order: 'desc',
      },
      options,
    },
  );
  if (!Array.isArray(page.result))
    throw new TypeError('Invalid Wiki conflict listing');
  const prefix = `${uri.replace(/\/+$/, '')}/`;
  const files = page.result
    .filter((entry) => !entry.isDir && entry.uri.startsWith(prefix))
    .map((entry) => entry.uri);
  return {
    kind: 'directory',
    data: {
      files,
      conflictFileCount: files.length,
      limited: page.hasMore || files.length >= 100,
    },
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
  return useDashboardQuery(load, requestOptions, enabled, refreshGeneration);
}
