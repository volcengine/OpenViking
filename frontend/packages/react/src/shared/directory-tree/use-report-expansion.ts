import { useEffect, useRef } from 'react';
import type { DirectoryQueries } from './directory-queries.js';
import type { DirectoryRow } from './rows.js';

export function useReportExpansion(
  session: DirectoryQueries | undefined,
  ready: boolean,
  rows: readonly DirectoryRow[],
  onChange?: (uris: readonly string[]) => void,
) {
  const last = useRef<{ session: DirectoryQueries; uris: string[] }>(undefined);
  // Sync the committed tree with the host's storage, including changes after
  // asynchronous directory loads. There is no subscription to dispose.
  useEffect(() => {
    if (!session || !ready || !onChange) return;
    const uris = rows.flatMap((row) =>
      row.kind === 'entry' && row.type === 'directory' && row.expanded
        ? [row.uri]
        : [],
    );
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
  }, [session, ready, rows, onChange]);
}
