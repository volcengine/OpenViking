import {
  isHomeResourceUri,
  isUserResourceUri,
  resourceUriIdentity,
} from '../resource-uri.js';
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
  {
    isExpanded,
    isDeferred,
    readHome,
    resolveHome,
    restoreListed,
  }: {
    isExpanded: (uri: string) => boolean;
    isDeferred: (uri: string) => boolean;
    readHome: () => string | undefined;
    resolveHome: (signal: AbortSignal) => Promise<string | undefined>;
    restoreListed: (uri: string) => void;
  },
  signal: AbortSignal,
): Promise<UnresolvedExpansion> {
  let home = readHome();
  let checkedHome = Boolean(home);
  const identify = (uri: string) => resourceUriIdentity(uri, home);
  const lookupHome = async () => {
    checkedHome = true;
    try {
      home = await resolveHome(signal);
    } catch {
      signal.throwIfAborted();
      // Matching equal spellings still works if the gateway cannot resolve ~.
    }
  };
  const targets = [...expanded].filter((uri) => uri !== rootUri);
  // Group mixed saved parent and child spellings before walking actual listings.
  if (
    !checkedHome &&
    targets.some(isHomeResourceUri) &&
    targets.some(isUserResourceUri)
  )
    await lookupHome();

  const root = identify(rootUri);
  const savedByKey = new Map(targets.map((uri) => [identify(uri), uri]));
  const children = new Map<string, Set<string>>();
  for (const [key, uri] of savedByKey) {
    // The tree can list a URI directly below a virtual ancestor. Only expanded
    // folders that were visible in the saved tree are present in the snapshot.
    const path = expansionPath(root, key);
    if (!path.length) continue;
    const parent = path
      .slice(0, -1)
      .reverse()
      .find((candidate) => savedByKey.has(candidate));
    const savedParent = (parent && savedByKey.get(parent)) || rootUri;
    const folders = children.get(savedParent) ?? new Set<string>();
    folders.add(uri);
    children.set(savedParent, folders);
  }

  const unresolved = new Map<string, string[]>();
  const preserve = (parent: string, wanted: ReadonlySet<string>) => {
    const pending = [...wanted];
    for (const uri of pending)
      for (const child of children.get(uri) ?? []) pending.push(child);
    unresolved.set(parent, [
      ...new Set([...(unresolved.get(parent) ?? []), ...pending]),
    ]);
  };
  const restore = async (
    parent: string,
    savedParent: string,
    page: DirState | undefined,
  ) => {
    const parentExpanded = () => isExpanded(parent) || isExpanded(savedParent);
    const wanted = children.get(savedParent);
    if (!wanted || !parentExpanded()) return;
    let indexedHome = home;
    const index = () => new Map([...wanted].map((uri) => [identify(uri), uri]));
    let wantedByKey = index();
    const wantsAlias = [...wanted].some(isHomeResourceUri);
    const wantsUserUri = [...wanted].some(isUserResourceUri);
    let sawAlias = false;
    let sawUserUri = false;
    let scanned = 0;
    // A failed later page can still supply previously loaded directory rows.
    while (page && !signal.aborted && parentExpanded()) {
      for (const entry of page.entries) {
        if (!home && !wantedByKey.has(identify(entry.uri))) {
          sawAlias ||= isHomeResourceUri(entry.uri);
          sawUserUri ||= isUserResourceUri(entry.uri);
          if (
            !checkedHome &&
            ((wantsAlias && sawUserUri) || (wantsUserUri && sawAlias))
          )
            await lookupHome();
        }
        if (indexedHome !== home) {
          indexedHome = home;
          wantedByKey = index();
        }
        const key = identify(entry.uri);
        const saved = wantedByKey.get(key);
        if (!saved) continue;
        if (isDeferred(saved)) {
          preserve(parent, new Set([saved]));
          continue;
        }
        if (!wanted.delete(saved)) continue;
        wantedByKey.delete(key);
        if (
          entry.isDir &&
          entry.access !== 'denied' &&
          !signal.aborted &&
          parentExpanded() &&
          isExpanded(saved)
        ) {
          restoreListed(entry.uri);
          await restore(entry.uri, saved, await load(entry.uri, 0, signal));
        }
      }
      if (page.error || !wanted.size || !page.hasMore) break;
      if (page.entries.length <= scanned) {
        if (parentExpanded()) preserve(parent, wanted);
        return;
      }
      scanned = page.entries.length;
      if (signal.aborted || !parentExpanded()) break;
      page = await load(parent, scanned, signal);
    }
    if (!wanted.size || !parentExpanded()) return;
    if (!page || page.error) {
      preserve(parent, wanted);
    } else if (!home) {
      // A mixed listing with an unresolved home cannot prove these paths absent.
      const uncertain = new Set(
        [...wanted].filter(
          (uri) =>
            (sawAlias && isUserResourceUri(uri)) ||
            (sawUserUri && isHomeResourceUri(uri)),
        ),
      );
      if (uncertain.size) preserve(parent, uncertain);
    }
  };
  await restore(rootUri, rootUri, rootPage);
  return unresolved;
}
