import type { FileTreeRow } from '../../components/file-tree/index.js';

/** Canonical identity for preview resource classification; preserve the scheme root. */
export const normalizeResourceUri = (uri: string) =>
  uri === 'viking://' ? uri : uri.replace(/\/+$/, '');

export function findSelectedRowId(
  rows: readonly FileTreeRow[],
  uri: string | null,
) {
  if (!uri) return uri;
  const target = normalizeResourceUri(uri);
  return (
    rows.find(
      (row) => row.kind === 'entry' && normalizeResourceUri(row.uri) === target,
    )?.id ?? uri
  );
}
