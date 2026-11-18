import { normalizeResourceUri } from '../resource-uri.js';
import type { DirectoryQueries } from './directory-queries.js';
import { expansionPath } from './expansion-path.js';
import type { DirState } from './types.js';

export type UnresolvedExpansion = ReadonlyMap<string, readonly string[]>;

/** Resolve saved folders from actual listings, including rows on later pages. */
export async function restoreDirectoryExpansion(
  load: DirectoryQueries['load'],
  rootUri: string,
  rootPage: DirState | undefined,
  expanded: ReadonlySet<string>,
  isExpanded: (uri: string) => boolean,
  signal: AbortSignal,
  onMatch: (savedUri: string, listedUri: string) => void,
): Promise<UnresolvedExpansion> {
  const root = normalizeResourceUri(rootUri);
  const children = new Map<string, Set<string>>();
  for (const uri of expanded) {
    if (uri === rootUri) continue;
    // The tree can list a URI directly below a virtual ancestor. Only expanded
    // folders that were visible in the saved tree are present in the snapshot.
    const parent =
      expansionPath(rootUri, uri)
        .slice(0, -1)
        .reverse()
        .find((candidate) => expanded.has(candidate)) ?? root;
    const folders = children.get(parent) ?? new Set<string>();
    folders.add(uri);
    children.set(parent, folders);
  }

  const unresolved = new Map<string, string[]>();
  const preserve = (parent: string, wanted: ReadonlySet<string>) => {
    const pending = [...wanted];
    for (const uri of pending)
      for (const child of children.get(uri) ?? []) pending.push(child);
    unresolved.set(parent, pending);
  };
  const restore = async (
    parent: string,
    savedParent: string,
    page: DirState | undefined,
  ) => {
    const parentExpanded = () => isExpanded(parent) || isExpanded(savedParent);
    const wanted = children.get(savedParent);
    if (!wanted || !parentExpanded()) return;
    let scanned = 0;
    // A failed later page can still supply previously loaded directory rows.
    while (page && !signal.aborted && parentExpanded()) {
      for (const entry of page.entries) {
        const savedUri = normalizeResourceUri(entry.uri);
        if (!wanted.delete(savedUri)) continue;
        if (
          entry.isDir &&
          entry.access !== 'denied' &&
          !signal.aborted &&
          parentExpanded() &&
          (isExpanded(entry.uri) || isExpanded(savedUri))
        ) {
          if (savedUri !== entry.uri) onMatch(savedUri, entry.uri);
          await restore(entry.uri, savedUri, await load(entry.uri, 0, signal));
        }
      }
      if (page.error || !wanted.size || !page.hasMore) break;
      if (page.entries.length <= scanned) {
        if (parentExpanded()) preserve(parent, wanted);
        break;
      }
      scanned = page.entries.length;
      if (signal.aborted || !parentExpanded()) break;
      page = await load(parent, scanned, signal);
    }
    if ((!page || page.error) && wanted.size && parentExpanded())
      preserve(parent, wanted);
  };
  await restore(rootUri, root, rootPage);
  return unresolved;
}
