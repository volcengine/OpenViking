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
  // Keep RFC 3986 reserved characters, percent, backslash, and ASCII controls escaped.
  return uri
    .split(/(\/|%(?:[01][\da-f]|2[13456789abcf]|3[abdf]|40|5[bcd]|7f))/i)
    .map((part, index) => (index % 2 === 0 ? decodePathLabel(part) : part))
    .join('');
}
