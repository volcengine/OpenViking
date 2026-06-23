const MEMORY_URI_PATTERNS = [
  /^viking:\/\/user\/(?:[^/]+(?:\/agent\/[^/]+)?\/)?memories(?:\/|$)/,
  /^viking:\/\/agent\/(?:[^/]+(?:\/user\/[^/]+)?\/)?memories(?:\/|$)/,
];

export function isMemoryUri(uri: string): boolean {
  return MEMORY_URI_PATTERNS.some((pattern) => pattern.test(uri));
}
