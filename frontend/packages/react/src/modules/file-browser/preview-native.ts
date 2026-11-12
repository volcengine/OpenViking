import { OV_PATHS } from '../../client/paths.js';
import { isOvClientError } from '../../client/errors.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { contentDigest } from './fs-content-digest.js';
import { splitWikiMetadata } from './wiki-metadata.js';
import type {
  CompileConfig,
  PreviewHistoryEntry,
} from './preview-workflow-types.js';

export async function readPreviewHistory(
  client: OvClient,
  uri: string,
  versioned: boolean,
  options: OvRequestOptions,
): Promise<PreviewHistoryEntry[]> {
  if (versioned) {
    try {
      const result = await client.request<{
        versions: {
          version_id: string;
          version_number: number;
          snapshot_id: string;
          snapshot_path: string;
          created_at: number;
          message?: string;
        }[];
      }>({
        method: 'GET',
        path: OV_PATHS.snapshotVersions,
        query: { uri, limit: 100 },
        options,
      });
      return result.versions.map((entry) => ({
        id: entry.version_id,
        snapshot: entry.snapshot_id,
        path: entry.snapshot_path,
        title: String(entry.version_number),
        numbered: true,
        note: entry.message,
        timestamp: entry.created_at,
      }));
    } catch (error) {
      // Some hosted data planes expose snapshot log but omit Compile's version index.
      if (!isOvClientError(error) || error.statusCode !== 404) throw error;
    }
  }
  try {
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
  } catch (error) {
    if (isOvClientError(error) && error.statusCode === 404) return [];
    throw error;
  }
}

export async function restorePreviewVersion(
  client: OvClient,
  uri: string,
  version: PreviewHistoryEntry,
  current: PreviewHistoryEntry,
  currentText: string,
  options: OvRequestOptions,
) {
  return client.request({
    method: 'POST',
    path: OV_PATHS.snapshotRestore,
    body: {
      path: uri,
      restore_commit_id: version.snapshot,
      current_commit_id: current.snapshot,
      expected_content_sha256: await contentDigest(currentText),
    },
    options,
  });
}

export async function readHistoricalText(
  client: OvClient,
  entry: PreviewHistoryEntry,
  options: OvRequestOptions,
  wiki = false,
) {
  const result = await client.request<Blob>({
    method: 'GET',
    path: OV_PATHS.snapshotShow,
    query: { target_ref: entry.snapshot, path: entry.path, raw: false },
    responseType: 'blob',
    options,
  });
  const text = await result.text();
  return wiki ? splitWikiMetadata(text).document : text;
}

export function readCompileConfig(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
) {
  return client.request<CompileConfig>({
    method: 'GET',
    path: OV_PATHS.compileConfig,
    query: { to: uri },
    options,
  });
}

export async function importPreviewPack(
  client: OvClient,
  uri: string,
  file: File,
  options: OvRequestOptions,
) {
  const body = new FormData();
  body.append('file', file);
  const upload = await client.request<{ temp_file_id: string }>({
    method: 'POST',
    path: OV_PATHS.resourceTempUpload,
    body,
    options,
  });
  // The import consumes the temporary upload, including failures. Every retry uploads the retained File again.
  return client.request<{ uri: string }>({
    method: 'POST',
    path: OV_PATHS.packImport,
    body: { temp_file_id: upload.temp_file_id, parent: uri },
    options,
  });
}

export function savePreviewBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  // Let the browser acquire the URL before releasing it.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export async function downloadPreview(
  client: OvClient,
  uri: string,
  pack: boolean,
  options: OvRequestOptions,
) {
  const blob = await client.request<Blob>(
    pack
      ? {
          method: 'POST',
          path: OV_PATHS.packExport,
          body: { uri },
          responseType: 'blob',
          options,
        }
      : {
          method: 'GET',
          path: OV_PATHS.contentDownload,
          query: { uri },
          responseType: 'blob',
          options,
        },
  );
  if (options.signal?.aborted) return;
  const name = uri.split('/').pop() || 'resource';
  savePreviewBlob(blob, pack ? `${name}.ovpack` : name);
}
