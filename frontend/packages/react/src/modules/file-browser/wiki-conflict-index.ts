import { OV_PATHS } from '../../client/paths.js';
import type { OvClient, OvRequestOptions } from '../../client/types.js';
import { within } from './dashboard-model.js';
import { resolveDashboardUri } from './dashboard-resource.js';
import { normalizeResourceUri } from './fs-uri.js';

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
    let offset = 0;
    let more: boolean;
    do {
      const page = await client.requestEnvelope<{ uri: string }[]>({
        method: 'GET',
        path: OV_PATHS.fsLs,
        query: {
          uri: root,
          recursive: true,
          output: 'agent',
          include_abstract: false,
          tags: ['has_conflict=true'],
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
        if (!within(path, root)) continue;
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
  return { roots: canonicalRoots, paths };
}
