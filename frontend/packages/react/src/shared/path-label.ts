/** Decode one URI segment for display only; never use the result as a resource key. */
export function decodePathLabel(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

/** Decode readable URI text while preserving escapes that affect path identity. */
export function formatVikingUriLabel(uri: string): string {
  if (!uri.startsWith('viking://')) return uri;
  return uri
    .split(/(\/|%(?:2f|5c|3f|23|25|[01][\da-f]|7f))/i)
    .map((part, index) => (index % 2 === 0 ? decodePathLabel(part) : part))
    .join('');
}
