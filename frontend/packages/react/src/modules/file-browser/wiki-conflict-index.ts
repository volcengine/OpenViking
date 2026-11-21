import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { within } from './dashboard-model.js';
import { resolveDashboardUri } from './dashboard-resource.js';
import { normalizeResourceUri } from '../../shared/resource-uri.js';
import type { FsEntry } from '../../shared/directory-tree/types.js';

/** Indexed files determine subtree presence without expanding the visible tree. */
export async function readWikiConflictIndex(
  client: OvClient,
  roots: readonly string[],
  options: OvRequestOptions,
) {
  const canonicalRoots: string[] = [];
  const paths = new Set<string>();
  for (const requested of roots) {
    const root = normalizeResourceUri(
      await resolveDashboardUri(client, requested, options),
    );
    canonicalRoots.push(root);
    const pending = [root];
    const visited = new Set<string>();
    while (pending.length) {
      const directory = pending.pop()!;
      if (visited.has(directory)) continue;
      visited.add(directory);
      let offset = 0;
      let more: boolean;
      do {
        // Native recursive listings have a fixed depth limit. Walk direct children
        // without tag filtering so untagged directories cannot hide deeper conflicts.
        const page = await client.requestEnvelope<FsEntry[]>({
          method: 'GET',
          path: OV_PATHS.fsLs,
          query: {
            uri: directory,
            recursive: false,
            output: 'agent',
            include_abstract: false,
            include_tags: true,
            limit: 100,
            offset,
          },
          options,
        });
        if (
          !Array.isArray(page.result) ||
          page.result.some((entry) => typeof entry?.uri !== 'string')
        )
          throw new TypeError('Invalid Wiki conflict listing');
        for (const entry of page.result) {
          let path = normalizeResourceUri(entry.uri);
          if (path.slice(0, path.lastIndexOf('/')) !== directory) continue;
          if (entry.access === 'denied') continue;
          if (entry.isDir) pending.push(path);
          if (!entry.tags?.includes('has_conflict=true')) continue;
          while (within(path, root)) {
            paths.add(path);
            if (path === root) break;
            path = path.slice(0, path.lastIndexOf('/'));
          }
        }
        more = page.hasMore;
        if (more && !page.result.length)
          throw new TypeError('Wiki conflict listing did not advance');
        offset += page.result.length;
      } while (more);
    }
  }
  return { roots: canonicalRoots, paths };
}
