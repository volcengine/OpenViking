export function normalizeTaskUri(value: unknown): string | undefined {
  const uri = typeof value === 'string' ? value.trim() : undefined;
  return uri && /^viking:\/\/[^\s<>"'`]+$/i.test(uri) ? uri : undefined;
}

/** Read the resource target from a submission or completion payload. */
export function readTaskResourceUri(value: unknown): string | undefined {
  if (!value || typeof value !== 'object') return;
  const result = value as Record<string, unknown>;
  for (const field of ['root_uri', 'uri', 'archive_uri', 'to']) {
    const uri = normalizeTaskUri(result[field]);
    if (uri) return uri;
  }
}
