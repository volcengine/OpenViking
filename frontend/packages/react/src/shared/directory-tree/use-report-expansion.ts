import { useEffect, useRef } from 'react';
import { normalizeResourceUri } from '../resource-uri.js';
import type { DirectoryQueries } from './directory-queries.js';
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
    const visible = new Map(
      rows.flatMap((row) =>
        row.kind === 'entry' && row.type === 'directory' && row.expanded
          ? [[normalizeResourceUri(row.uri), row.uri] as const]
          : [],
      ),
    );
    const saved = new Map(visible);
    // Collapsing a branch removes it from the visible rows. Only still-open
    // branches carry their unresolved paths into the published snapshot.
    for (const [parent, pending] of unresolved) {
      if (parent !== rootUri && !visible.has(normalizeResourceUri(parent)))
        continue;
      for (const uri of pending) {
        const key = normalizeResourceUri(uri);
        if (!saved.has(key)) saved.set(key, uri);
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
