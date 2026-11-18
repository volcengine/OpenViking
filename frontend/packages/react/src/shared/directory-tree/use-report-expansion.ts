import { useEffect, useRef } from 'react';
import {
  HOME_URI,
  needsHomeIdentity,
  normalizeResourceUri,
} from '../resource-uri.js';
import type { DirectoryQueries } from './directory-queries.js';
import { expansionPath } from './expansion-path.js';
import type { UnresolvedExpansion } from './restore-expansion.js';
import type { DirectoryRow } from './rows.js';
import type { useDirectoryExpansion } from './use-directory-expansion.js';

function homeRelativePath(uri: string) {
  return normalizeResourceUri(uri).replace(
    /^viking:\/\/user\/[^/]+(?=\/|$)/,
    HOME_URI,
  );
}

// Relative paths only detect uncertainty. An authenticated home decides identity.
function mayBeFolded(uri: string, collapsed: ReadonlySet<string>) {
  return [...collapsed].some((fold) => {
    if (!needsHomeIdentity(fold, uri)) return false;
    const ancestor = homeRelativePath(fold);
    const target = homeRelativePath(uri);
    return target === ancestor || target.startsWith(`${ancestor}/`);
  });
}

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
    // Keep the fold in this session and defer publication until Refresh or
    // another home lookup can establish which canonical branch it affected.
    if (
      collapsed.size &&
      identifyUri(HOME_URI) === HOME_URI &&
      uris.some((uri) => mayBeFolded(uri, collapsed))
    )
      return;
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
