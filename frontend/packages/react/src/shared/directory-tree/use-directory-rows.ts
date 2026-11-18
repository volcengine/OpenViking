import { useEffect, useMemo } from 'react';
import type { DirectoryQueries } from './directory-queries.js';
import { flattenTree } from './rows.js';
import type { DirState } from './types.js';

export function useDirectoryRows({
  session,
  rootUri,
  dirs,
  expanded,
  identifyUri,
  initializing,
}: {
  session?: DirectoryQueries;
  rootUri: string;
  dirs: Record<string, DirState>;
  expanded: ReadonlySet<string>;
  identifyUri: (uri: string) => string;
  initializing: boolean;
}) {
  const { rows, sources } = useMemo(() => {
    const expandedKeys = new Set([...expanded].map(identifyUri));
    const listedExpansion = new Set<string>();
    const available = new Map<string, string>();
    for (const [uri, dir] of Object.entries(dirs)) {
      if (dir.ready && !dir.error) available.set(identifyUri(uri), uri);
    }

    const view = { ...dirs };
    const sources = new Map<string, string>();
    for (const dir of Object.values(dirs)) {
      for (const entry of dir.entries) {
        if (!entry.isDir) continue;
        const key = identifyUri(entry.uri);
        if (!expandedKeys.has(key)) continue;
        listedExpansion.add(entry.uri);
        const source = available.get(key);
        if (source && source !== entry.uri && !dirs[entry.uri]?.ready) {
          view[entry.uri] = dirs[source]!;
          sources.set(entry.uri, source);
        }
      }
    }
    return {
      rows: flattenTree(rootUri, 0, view, listedExpansion),
      sources,
    };
  }, [rootUri, dirs, expanded, identifyUri]);

  // A server refresh can change the spelling of a visible URI without an event.
  // Bind its real query after commit. The directory session owns its observer and
  // releases the subscription on scope change or unmount.
  useEffect(() => {
    if (!session || initializing || !sources.size) return;
    for (const row of rows) {
      if (row.kind !== 'entry' || !row.expanded) continue;
      const source = sources.get(row.uri);
      if (source) session.adoptListing(row.uri, source);
    }
  }, [session, initializing, rows, sources]);
  return rows;
}
