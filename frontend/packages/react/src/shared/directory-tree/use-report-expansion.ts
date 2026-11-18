import { useEffect, useRef } from 'react';
import { normalizeResourceUri } from '../resource-uri.js';
import type { DirectoryQueries } from './directory-queries.js';
import { expansionPath } from './expansion-path.js';
import type { UnresolvedExpansion } from './restore-expansion.js';
import type { DirectoryRow } from './rows.js';

export function useReportExpansion(
  session: DirectoryQueries | undefined,
  ready: boolean,
  rootUri: string,
  rows: readonly DirectoryRow[],
  unresolved: UnresolvedExpansion,
  onChange?: (uris: readonly string[]) => void,
) {
  const last = useRef<{ session: DirectoryQueries; uris: string[] }>(undefined);
  // Sync the committed tree with the host's storage, including changes after
  // asynchronous directory loads. There is no subscription to dispose.
  useEffect(() => {
    if (!session || !ready || !onChange) return;
    const visible = new Map<string, string>();
    const collapsed = new Set<string>();
    for (const row of rows) {
      if (row.kind !== 'entry' || row.type !== 'directory') continue;
      const uri = normalizeResourceUri(row.uri);
      if (row.expanded) visible.set(uri, row.uri);
      else collapsed.add(uri);
    }
    const saved = new Map(visible);
    // A later page can reveal a branch that is still pending under the root.
    // Honor its current collapse state and keep pending paths elsewhere.
    for (const [parent, pending] of unresolved) {
      if (parent !== rootUri && !visible.has(normalizeResourceUri(parent)))
        continue;
      for (const uri of pending) {
        const key = normalizeResourceUri(uri);
        if (
          !saved.has(key) &&
          !expansionPath(rootUri, key).some((path) => collapsed.has(path))
        )
          saved.set(key, uri);
      }
    }
    const uris = [...saved.values()];
    const previous = last.current;
    if (
      previous?.session === session &&
      previous.uris.length === uris.length &&
      uris.every((uri, index) => uri === previous.uris[index])
    ) {
      return;
    }
    last.current = { session, uris };
    onChange(uris);
  }, [session, ready, rootUri, rows, unresolved, onChange]);
}
