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
import type { DirectoryExpansionSnapshot } from './types.js';

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

function sameUris(
  left: readonly string[] | undefined,
  right: readonly string[],
) {
  return (
    left?.length === right.length &&
    right.every((uri, index) => left[index] === uri)
  );
}

export function useReportExpansion(
  session: DirectoryQueries | undefined,
  ready: boolean,
  rootUri: string,
  rows: readonly DirectoryRow[],
  unresolved: UnresolvedExpansion,
  {
    collapsed,
    expanded,
    pendingEdits,
    identifyUri,
  }: Pick<
    ReturnType<typeof useDirectoryExpansion>,
    'collapsed' | 'expanded' | 'pendingEdits' | 'identifyUri'
  >,
  reconciling: boolean,
  onChange?: (uris: readonly string[]) => void,
  onSnapshotChange?: (snapshot: DirectoryExpansionSnapshot) => void,
) {
  const last = useRef<{
    session: DirectoryQueries;
    legacy?: readonly string[];
    snapshot?: DirectoryExpansionSnapshot;
  }>(undefined);
  // Sync the committed tree with the host's storage, including changes after
  // asynchronous directory loads. There is no subscription to dispose.
  useEffect(() => {
    if (!session || !ready || (!onChange && !onSnapshotChange)) return;
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
    if (reconciling) {
      // The replacement listing is not ready to settle the old expansion.
      for (const uri of expanded) {
        const key = identifyUri(uri);
        if (key !== root && !saved.has(key)) saved.set(key, uri);
      }
    }

    const uris = [...saved.values()];
    const previous =
      last.current?.session === session ? last.current : { session };
    last.current = previous;
    if (onSnapshotChange) {
      const snapshot = {
        expandedUris: uris,
        collapsedUris: [...collapsed],
        pendingEdits,
      };
      if (
        !sameUris(previous.snapshot?.expandedUris, snapshot.expandedUris) ||
        !sameUris(previous.snapshot?.collapsedUris, snapshot.collapsedUris) ||
        JSON.stringify(previous.snapshot?.pendingEdits ?? []) !==
          JSON.stringify(pendingEdits)
      ) {
        previous.snapshot = snapshot;
        onSnapshotChange(snapshot);
      }
    }

    // The URI-only callback cannot carry a fold across an unresolved alias.
    if (!onChange || reconciling || sameUris(previous.legacy, uris)) return;
    if (
      collapsed.size &&
      identifyUri(HOME_URI) === HOME_URI &&
      uris.some((uri) => mayBeFolded(uri, collapsed))
    )
      return;
    previous.legacy = uris;
    onChange(uris);
  }, [
    session,
    ready,
    rootUri,
    rows,
    unresolved,
    collapsed,
    expanded,
    pendingEdits,
    identifyUri,
    reconciling,
    onChange,
    onSnapshotChange,
  ]);
}
