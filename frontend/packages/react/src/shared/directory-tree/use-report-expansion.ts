import { useEffect, useRef } from 'react';
import type { DirectoryQueries } from './directory-queries.js';
import { expansionPath } from './expansion-path.js';
import type { UnresolvedExpansion } from './restore-expansion.js';
import type { DirectoryRow } from './rows.js';
import type { useDirectoryExpansion } from './use-directory-expansion.js';

export function useReportExpansion(
  session: DirectoryQueries | undefined,
  ready: boolean,
  rootUri: string,
  rows: readonly DirectoryRow[],
  unresolved: UnresolvedExpansion,
  {
    collapsed,
    identifyUri,
  }: Pick<
    ReturnType<typeof useDirectoryExpansion>,
    'collapsed' | 'identifyUri'
  >,
  onChange?: (uris: readonly string[]) => void,
) {
  const last = useRef<{ session: DirectoryQueries; uris: string[] }>(undefined);
  // Sync the committed tree with the host's storage, including changes after
  // asynchronous directory loads. There is no subscription to dispose.
  useEffect(() => {
    if (!session || !ready || !onChange) return;
    const visible = new Map<string, string>();
    for (const row of rows) {
      if (row.kind === 'entry' && row.type === 'directory' && row.expanded)
        visible.set(identifyUri(row.uri), row.uri);
    }
    const saved = new Map(visible);
    const root = identifyUri(rootUri);
    const collapsedKeys = new Set([...collapsed].map(identifyUri));
    for (const [parent, pending] of unresolved) {
      const parentKey = identifyUri(parent);
      if (parentKey !== root && !visible.has(parentKey)) continue;
      for (const uri of pending) {
        const key = identifyUri(uri);
        if (
          !saved.has(key) &&
          !expansionPath(root, key).some((path) => collapsedKeys.has(path))
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
  }, [
    session,
    ready,
    rootUri,
    rows,
    unresolved,
    collapsed,
    identifyUri,
    onChange,
  ]);
}
