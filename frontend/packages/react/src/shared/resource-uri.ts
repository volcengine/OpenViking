/** Normalize trailing slashes without changing the scheme root or home alias. */
export const normalizeResourceUri = (uri: string) =>
  uri === 'viking://' ? uri : uri.replace(/\/+$/, '');

export const isSystemUri = (uri: string) =>
  /^viking:\/\/system(?:\/|$)/i.test(normalizeResourceUri(uri));

export const HOME_URI = 'viking://~';
export const isHomeResourceUri = (uri: string) =>
  uri === HOME_URI || uri.startsWith(`${HOME_URI}/`);

/** Use canonical identity for comparisons only; requests keep their original spelling. */
export function resourceUriIdentity(uri: string, homeRoot?: string) {
  const normalized = normalizeResourceUri(uri);
  return homeRoot && isHomeResourceUri(normalized)
    ? `${homeRoot}${normalized.slice(HOME_URI.length)}`
    : normalized;
}

export const isUserResourceUri = (uri: string) =>
  uri === 'viking://user' || uri.startsWith('viking://user/');

export const needsHomeIdentity = (left: string, right: string) =>
  (isHomeResourceUri(left) && isUserResourceUri(right)) ||
  (isHomeResourceUri(right) && isUserResourceUri(left));
