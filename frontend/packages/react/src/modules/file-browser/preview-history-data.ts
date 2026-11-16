import { OV_PATHS } from '../../client/paths.js';
import { isOvClientError } from '../../client/errors.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import type { PreviewHistoryEntry } from './preview-workflow-types.js';

interface VersionPage {
  versions: {
    version_id: string;
    version_number: number;
    snapshot_id: string;
    snapshot_path: string;
    created_at: number;
    message?: string;
  }[];
  next_before_version?: string | null;
}
export async function readPreviewHistory(
  client: OvClient,
  uri: string,
  versioned: boolean,
  options: OvRequestOptions,
): Promise<PreviewHistoryEntry[]> {
  if (versioned) {
    try {
      const entries: PreviewHistoryEntry[] = [];
      let before: string | undefined;
      do {
        const page = await client.request<VersionPage>({
          method: 'GET',
          path: OV_PATHS.snapshotVersions,
          query: { uri, limit: 100, before_version: before },
          options,
        });
        entries.push(
          ...page.versions.map((entry) => ({
            id: entry.version_id,
            snapshot: entry.snapshot_id,
            path: entry.snapshot_path,
            title: String(entry.version_number),
            numbered: true,
            note: entry.message,
            timestamp: entry.created_at,
          })),
        );
        before = page.next_before_version ?? undefined;
      } while (before);
      return entries;
    } catch (error) {
      // Hosted kernels without a retained-version index expose their file Git log instead.
      if (!isOvClientError(error) || error.statusCode !== 404) throw error;
    }
  }
  const result = await client.request<
    { oid: string; message: string; author: { time_seconds: number } }[]
  >({
    method: 'GET',
    path: OV_PATHS.snapshotLog,
    query: { paths: [uri], limit: 500 },
    options,
  });
  return result.map((entry) => ({
    id: entry.oid,
    snapshot: entry.oid,
    path: uri,
    title: entry.message,
    note: entry.message,
    timestamp: entry.author.time_seconds,
  }));
}
