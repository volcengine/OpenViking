import type { DirectoryQueries } from './directory-queries.js';
import { expandChain } from './fs-path.js';
import type { DirState } from './types.js';

/** Resolve saved folders from actual listings, including rows on later pages. */
export async function restoreDirectoryExpansion(
  load: DirectoryQueries['load'],
  rootUri: string,
  rootPage: DirState | undefined,
  expanded: ReadonlySet<string>,
  isExpanded: (uri: string) => boolean,
  signal: AbortSignal,
) {
  const children = new Map<string, Set<string>>();
  for (const uri of expanded) {
    if (uri === rootUri) continue;
    // The tree can list a URI directly below a virtual ancestor. Only expanded
    // folders that were visible in the saved tree are present in the snapshot.
    const parent =
      expandChain(rootUri, uri)
        .slice(0, -1)
        .reverse()
        .find((candidate) => expanded.has(candidate)) ?? rootUri;
    const folders = children.get(parent) ?? new Set<string>();
    folders.add(uri);
    children.set(parent, folders);
  }

  const restore = async (parent: string, page: DirState | undefined) => {
    const wanted = children.get(parent);
    if (!wanted) return;
    let scanned = 0;
    while (page && !page.error && !signal.aborted && isExpanded(parent)) {
      for (const entry of page.entries) {
        if (!wanted.delete(entry.uri)) continue;
        if (
          entry.isDir &&
          entry.access !== 'denied' &&
          !signal.aborted &&
          isExpanded(parent) &&
          isExpanded(entry.uri)
        ) {
          const childPage = await load(entry.uri, 0, signal);
          await restore(entry.uri, childPage);
        }
      }
      if (!wanted.size || !page.hasMore || page.entries.length <= scanned)
        break;
      scanned = page.entries.length;
      if (signal.aborted || !isExpanded(parent)) break;
      page = await load(parent, scanned, signal);
    }
  };
  await restore(rootUri, rootPage);
}
