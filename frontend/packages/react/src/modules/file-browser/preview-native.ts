import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { splitWikiMetadata } from './wiki-metadata.js';
import type {
  CompileConfig,
  PreviewHistoryEntry,
} from './preview-workflow-types.js';

export function savePreviewSnapshot(
  client: OvClient,
  uri: string,
  message: string,
  options: OvRequestOptions,
) {
  return client.request<{ result: 'created' | 'noop' }>({
    method: 'POST',
    path: OV_PATHS.snapshotCommit,
    body: { message, paths: [uri], branch: 'main' },
    options,
  });
}

export { readPreviewHistory } from './preview-history-data.js';

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
  compileName?: string,
) {
  return client.request<CompileConfig>({
    method: 'GET',
    path: OV_PATHS.compileConfig,
    query: compileName ? { compile_name: compileName } : { to: uri },
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
