import { OV_PATHS } from '../../client/paths.js';
import { isOvClientError } from '../../client/errors.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { splitWikiMetadata } from './wiki-metadata.js';
import type { PreviewHistoryEntry } from './preview-workflow-types.js';

interface NativeCommit {
  oid: string;
  message: string;
  author?: { time_seconds: number };
  committer?: { time_seconds: number };
}
interface VersionPage {
  uri?: string;
  versions: (
    | NativeCommit
    | {
        version_id: string;
        version_number: number;
        snapshot_id: string;
        snapshot_path: string;
        created_at: number;
        message?: string;
      }
  )[];
  next_before_snapshot?: string | null;
  next_before_version?: string | null;
}

/** A null result is a confirmed deletion. Other missing content stays an error. */
export async function readHistoricalText(
  client: OvClient,
  entry: PreviewHistoryEntry,
  options: OvRequestOptions,
  wiki: boolean,
): Promise<string | null> {
  const path = entry.path;
  try {
    const blob = await client.request<Blob>({
      method: 'GET',
      path: OV_PATHS.snapshotShow,
      query: { target_ref: entry.snapshot, path, raw: false },
      responseType: 'blob',
      options,
    });
    const text = await blob.text();
    return wiki ? splitWikiMetadata(text).document : text;
  } catch (error) {
    if (
      !isOvClientError(error) ||
      error.statusCode !== 404 ||
      error.code !== 'NOT_FOUND' ||
      !error.details ||
      typeof error.details !== 'object' ||
      !('type' in error.details) ||
      error.details.type !== 'git_blob' ||
      !('resource' in error.details) ||
      typeof error.details.resource !== 'string'
    ) {
      throw error;
    }
    const reason = 'reason' in error.details ? error.details.reason : undefined;
    if ('deleted_file' in error.details) {
      if (reason === 'path_not_found' && error.details.deleted_file === true)
        return null;
      throw error;
    }
    if (reason !== undefined && reason !== 'path_not_found') throw error;

    // Older gateways can report a missing path without the first parent's type.
    // Confirm that a file was deleted from that parent using the legacy diff.
    const [commit] = await client.request<{ oid: string; parents: string[] }[]>(
      {
        method: 'GET',
        path: OV_PATHS.snapshotLog,
        query: { branch: entry.snapshot, paths: [path], limit: 1 },
        options,
      },
    );
    const parent = commit?.parents?.[0];
    if (commit?.oid !== entry.snapshot || !parent) throw error;

    const diff = await client
      .request<{
        change_type: string;
        from_commit: string;
        to_commit: string;
      }>({
        method: 'GET',
        path: OV_PATHS.snapshotDiff,
        query: { path, from: parent, to: entry.snapshot },
        options,
      })
      .catch((cause: unknown) => {
        if (isOvClientError(cause) && cause.statusCode === 404) throw error;
        throw cause;
      });
    if (
      diff?.change_type === 'deleted' &&
      diff.from_commit === parent &&
      diff.to_commit === entry.snapshot
    ) {
      return null;
    }
    throw error;
  }
}

/** The native file restore writes this message on the new commit. */
function isRestoreCommit(message: string | undefined, uri: string): boolean {
  const prefix = `restore ${uri} to `;
  if (!message?.startsWith(prefix)) return false;
  const source = message.slice(prefix.length);
  return source.length === 40 && /^[0-9a-f]{40}$/.test(source);
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
      let cursorField = 'before_snapshot';
      const seen = new Set<string>();
      do {
        const page = await client.request<VersionPage>({
          method: 'GET',
          path: OV_PATHS.snapshotVersions,
          query: { uri, limit: 100, [cursorField]: before },
          options,
        });
        const fileUri = page.uri ?? uri;
        entries.push(
          ...page.versions.map((entry) =>
            'oid' in entry
              ? {
                  id: entry.oid,
                  snapshot: entry.oid,
                  path: uri,
                  title: entry.message,
                  note: entry.message,
                  restored: isRestoreCommit(entry.message, fileUri),
                  timestamp:
                    entry.committer?.time_seconds ?? entry.author?.time_seconds,
                }
              : {
                  id: entry.version_id,
                  snapshot: entry.snapshot_id,
                  path: entry.snapshot_path,
                  title: String(entry.version_number),
                  numbered: true,
                  note: entry.message,
                  restored: isRestoreCommit(entry.message, fileUri),
                  timestamp: entry.created_at,
                },
          ),
        );
        cursorField =
          'next_before_snapshot' in page ? 'before_snapshot' : 'before_version';
        before =
          page.next_before_snapshot ?? page.next_before_version ?? undefined;
        if (before && seen.has(before))
          throw new TypeError('Snapshot cursor did not advance');
        if (before) seen.add(before);
      } while (before);
      return entries;
    } catch (error) {
      // Hosted kernels without a retained-version index expose their file Git log instead.
      if (!isOvClientError(error) || error.statusCode !== 404) throw error;
    }
  }
  const result = await client.request<NativeCommit[]>({
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
    restored: versioned && isRestoreCommit(entry.message, uri),
    timestamp: entry.committer?.time_seconds ?? entry.author?.time_seconds,
  }));
}
