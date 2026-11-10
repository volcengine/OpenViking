/** Search results may repeat a URI; preserve the matching row's scoped identifier. */
export function selectedRowId(
  uri: string | null,
  results: readonly { uri: string }[] | null,
) {
  if (!uri || results === null) return uri;
  const index = results.findIndex((entry) => entry.uri === uri);
  return index < 0 ? uri : `${uri}#${index}`;
}
