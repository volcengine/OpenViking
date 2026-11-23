import { useEffect, useMemo } from 'react';
import {
  HOME_URI,
  isHomeResourceUri,
  isUserResourceUri,
  normalizeResourceUri,
} from '../resource-uri.js';
import type { DirectoryQueries } from './directory-queries.js';
import { flattenTree } from './rows.js';
import { directoryView } from './entry-visibility.js';
import type { DirectoryExpansionEdit, DirState } from './types.js';

const homeRelativePath = (uri: string) =>
  normalizeResourceUri(uri).replace(
    /^viking:\/\/user\/[^/]+(?=\/|$)/,
    HOME_URI,
  );

const hasListing = (dir?: DirState) => Boolean(dir?.ready && !dir.error);

function containsPath(ancestors: readonly string[], path: string) {
  return ancestors.some(
    (ancestor) => path === ancestor || path.startsWith(`${ancestor}/`),
  );
}

export function useDirectoryRows({
  session,
  rootUri,
  dirs,
  hideDeniedEntries: hide,
  expanded,
  collapsed,
  pendingEdits,
  identifyUri,
  resolveHome,
  initializing,
}: {
  session?: DirectoryQueries;
  rootUri: string;
  dirs: Record<string, DirState>;
  hideDeniedEntries?: boolean;
  expanded: ReadonlySet<string>;
  collapsed: ReadonlySet<string>;
  pendingEdits: readonly DirectoryExpansionEdit[];
  identifyUri: (uri: string) => string;
  resolveHome: (signal: AbortSignal) => Promise<string | undefined>;
  initializing: boolean;
}) {
  const { rows, replacements, retirements, homeCandidate, foldCandidate } =
    useMemo(() => {
      const expansionByKey = new Map(
        [...expanded].map((uri) => [identifyUri(uri), uri]),
      );
      const unknownHome = identifyUri(HOME_URI) === HOME_URI;
      const expandedAliases = new Set(
        unknownHome
          ? [...expanded].filter(isHomeResourceUri).map(homeRelativePath)
          : [],
      );
      const expandedUserPaths = new Set(
        unknownHome
          ? [...expanded].filter(isUserResourceUri).map(homeRelativePath)
          : [],
      );
      const foldedAliases = unknownHome
        ? [...collapsed].filter(isHomeResourceUri).map(homeRelativePath)
        : [];
      const foldedUserPaths = unknownHome
        ? [...collapsed].filter(isUserResourceUri).map(homeRelativePath)
        : [];
      const listedExpansion = new Set<string>();
      const ambiguous = new Set<string>();
      const ambiguousFolds = new Set<string>();
      const observers = new Map<string, string[]>();
      for (const uri of Object.keys(dirs)) {
        const key = identifyUri(uri);
        const equivalents = observers.get(key) ?? [];
        equivalents.push(uri);
        observers.set(key, equivalents);
      }

      const view = directoryView(dirs, hide);
      const sources = new Map<string, string>();
      for (const dir of Object.values(dirs)) {
        for (const entry of dir.entries) {
          const { uri, isDir, access } = entry;
          if (!isDir || access === 'denied') continue;
          const key = identifyUri(uri);
          const expandedUri = expansionByKey.get(key);
          const isAlias = isHomeResourceUri(uri);
          const isUserUri = isUserResourceUri(uri);
          const homePath = homeRelativePath(uri);
          const reopened = pendingEdits.some(
            (edit) => edit.expanded && identifyUri(edit.uri) === key,
          );
          if (
            (isAlias && containsPath(foldedUserPaths, homePath)) ||
            (isUserUri && containsPath(foldedAliases, homePath))
          ) {
            // A reopen can display this row while its earlier fold still
            // needs the scoped identity to settle the persisted edit order.
            ambiguousFolds.add(uri);
            if (!reopened) continue;
          }
          if (!expandedUri) {
            if (
              (isAlias && expandedUserPaths.has(homePath)) ||
              (isUserUri && expandedAliases.has(homePath))
            )
              ambiguous.add(uri);
            continue;
          }
          listedExpansion.add(uri);
          const current = dirs[uri];
          const equivalents = observers.get(key) ?? [];
          if (hasListing(current) && !current?.loading) {
            const previous = equivalents.find((other) => other !== uri);
            if (previous) sources.set(uri, previous);
            continue;
          }
          const cached = equivalents.find(
            (previous) => previous !== uri && hasListing(dirs[previous]),
          );
          const source =
            cached ??
            equivalents.find((previous) => previous !== uri) ??
            (expandedUri !== uri || !current ? expandedUri : undefined);
          if (!source) continue;
          sources.set(uri, source);
          if (current?.error || hasListing(current)) continue;
          // Only retain cached children here. Paging controls belong to the
          // raw URI observer once it is attached to the new listed spelling.
          view[uri] = {
            entries: view[source]?.entries ?? [],
            ready: false,
            loading: true,
            hasMore: false,
            error: null,
          };
        }
      }
      const rows = flattenTree(rootUri, 0, view, listedExpansion);
      const visible = new Set(
        rows.flatMap((row) =>
          row.kind === 'entry' && row.type === 'directory' ? [row.uri] : [],
        ),
      );
      const retirements = new Set<string>();
      for (const row of rows) {
        if (row.kind !== 'entry' || row.type !== 'directory' || row.expanded)
          continue;
        for (const previous of observers.get(identifyUri(row.uri)) ?? []) {
          if (previous === rootUri || visible.has(previous)) continue;
          const branch = identifyUri(previous);
          for (const uri of Object.keys(dirs)) {
            const key = identifyUri(uri);
            if (
              uri !== rootUri &&
              !visible.has(uri) &&
              (key === branch || key.startsWith(`${branch}/`))
            )
              retirements.add(uri);
          }
        }
      }
      return {
        rows,
        retirements,
        replacements: [...sources].flatMap(([uri, source]) =>
          visible.has(uri)
            ? [
                {
                  uri,
                  source,
                  retirePrevious: source !== rootUri && !visible.has(source),
                },
              ]
            : [],
        ),
        homeCandidate: [...ambiguous].find((uri) => visible.has(uri)),
        foldCandidate: [...ambiguousFolds].find((uri) => visible.has(uri)),
      };
    }, [rootUri, dirs, hide, expanded, collapsed, pendingEdits, identifyUri]);

  const rootLoading = dirs[rootUri]?.loading ?? false;
  const lookupCandidate =
    foldCandidate ?? (initializing ? undefined : homeCandidate);
  // A spelling match only suggests a home lookup. Use its scoped result
  // before reconciling the expanded row or reporting an updated snapshot.
  useEffect(() => {
    if (!session || rootLoading || !lookupCandidate) return;
    const controller = new AbortController();
    void resolveHome(controller.signal).catch(() => undefined);
    return () => controller.abort();
  }, [session, rootLoading, lookupCandidate, resolveHome]);

  // A server refresh can change the spelling of a visible URI without an event.
  // Bind its real query after commit. The directory session owns its observer and
  // releases the subscription on scope change or unmount.
  useEffect(() => {
    if (!session || initializing) return;
    for (const { uri, source, retirePrevious } of replacements)
      if (retirePrevious || !hasListing(dirs[uri]))
        session.adoptListing(uri, source, retirePrevious);
    for (const uri of retirements) session.retireListing(uri);
  }, [session, initializing, replacements, retirements, dirs]);
  return {
    rows,
    reconciling:
      Boolean(homeCandidate || foldCandidate) ||
      replacements.some(
        ({ uri }) => !hasListing(dirs[uri]) || dirs[uri]?.loading,
      ),
  };
}
