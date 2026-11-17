/** Normalize trailing slashes without changing the scheme root or home alias. */
export const normalizeResourceUri = (uri: string) =>
  uri === 'viking://' ? uri : uri.replace(/\/+$/, '');

export const isSystemUri = (uri: string) =>
  /^viking:\/\/system(?:\/|$)/i.test(normalizeResourceUri(uri));
