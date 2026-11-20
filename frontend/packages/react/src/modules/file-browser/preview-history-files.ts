import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { loadPage } from '../../shared/directory-tree/fs-page.js';
import { isBinaryFile } from './fs-file-kind.js';
import { supportsFileHistory } from './preview-policy.js';

/** Traverse the selected subtree; every directory page keeps its native error and abort semantics. */
export async function readHistoryFiles(
  client: OvClient,
  uri: string,
  options: OvRequestOptions,
) {
  const files: string[] = [];
  const directories = [uri];
  const visited = new Set<string>();
  for (const directory of directories) {
    if (visited.has(directory)) continue;
    visited.add(directory);
    let offset = 0;
    let more = true;
    while (more) {
      options.signal?.throwIfAborted();
      const page = await loadPage(
        client,
        directory,
        offset,
        options.signal!,
        options,
      );
      for (const entry of page.entries) {
        if (entry.access === 'denied') continue;
        if (entry.isDir) directories.push(entry.uri);
        else if (supportsFileHistory(entry.uri) && !isBinaryFile(entry.uri))
          files.push(entry.uri);
      }
      offset += page.entries.length;
      more = page.hasMore && page.entries.length > 0;
    }
  }
  return files.sort((a, b) => a.localeCompare(b));
}
