import type { FileTreeRow } from '../../components/file-tree/index.js';

/** Normalize trailing slashes without changing the scheme root or home alias. */
export const normalizeResourceUri = (uri: string) =>
  uri === 'viking://' ? uri : uri.replace(/\/+$/, '');

const HOME_URI = 'viking://~';
export const isHomeResourceUri = (uri: string) =>
  uri === HOME_URI || uri.startsWith(`${HOME_URI}/`);

/** Use canonical identity for comparisons only; requests keep their original spelling. */
export function resourceUriIdentity(uri: string, homeRoot?: string) {
  const normalized = normalizeResourceUri(uri);
  return homeRoot && isHomeResourceUri(normalized)
    ? `${homeRoot}${normalized.slice(HOME_URI.length)}`
    : normalized;
}

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
