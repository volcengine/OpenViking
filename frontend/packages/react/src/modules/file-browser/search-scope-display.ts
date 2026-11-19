/** Decode directory labels without changing escaped separators or request URIs. */
export function displayScopeUri(uri: string): string {
  return uri
    .split('/')
    .map((segment) => {
      try {
        return decodeURI(segment);
      } catch {
        // Literal percent signs and malformed escapes are valid display content.
        return segment;
      }
    })
    .join('/');
}
