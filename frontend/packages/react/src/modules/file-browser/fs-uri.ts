/** Canonical identity for preview resource classification; preserve the scheme root. */
export const normalizeResourceUri = (uri: string) =>
  uri === 'viking://' ? uri : uri.replace(/\/+$/, '');
