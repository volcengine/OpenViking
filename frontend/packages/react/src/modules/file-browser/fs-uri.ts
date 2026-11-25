import type { FileTreeRow } from '../../components/file-tree/index.js';

export function findSelectedRowId(
  rows: readonly FileTreeRow[],
  uri: string | null,
  identifyUri: (uri: string) => string,
) {
  if (!uri) return uri;
  const target = identifyUri(uri);
  return (
    rows.find((row) => row.kind === 'entry' && identifyUri(row.uri) === target)
      ?.id ?? uri
  );
}
