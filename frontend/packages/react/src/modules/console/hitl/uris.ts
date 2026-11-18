const scheme = 'viking://';

/** Validate and normalize a literal URI before it is used by the form or sent. */
export function parseCompileUri(value: string): string | undefined {
  const uri = value.trim().replace(/\/$/, '');
  if (
    !uri.startsWith(scheme) ||
    !uri.slice(scheme.length) ||
    /[\s?#\\]/u.test(uri)
  )
    return;
  try {
    const safe = uri
      .slice(scheme.length)
      .split('/')
      .every((segment) => {
        const decoded = decodeURIComponent(segment);
        return (
          Boolean(decoded) &&
          decoded !== '.' &&
          decoded !== '..' &&
          !Array.from(decoded).some((character) => {
            const code = character.charCodeAt(0);
            return (
              code < 32 ||
              code === 127 ||
              character === '/' ||
              character === '\\'
            );
          })
        );
      });
    if (safe) return uri;
  } catch {
    return;
  }
}

export function parseSourceUris(value: string): string[] | undefined {
  const result = new Set<string>();
  for (const line of value.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const uri = parseCompileUri(line);
    if (!uri) return;
    result.add(uri);
  }
  return [...result];
}

export function overlapsSource(
  target: string,
  sources: readonly string[],
  userId?: string,
) {
  const home = `${scheme}~`;
  const identity = (uri: string) =>
    userId && (uri === home || uri.startsWith(home + '/'))
      ? `${scheme}user/${userId}${uri.slice(home.length)}`
      : uri;
  const destination = identity(target);
  return sources.some((source) => {
    const candidate = identity(source);
    return (
      candidate === destination ||
      candidate.startsWith(destination + '/') ||
      destination.startsWith(candidate + '/')
    );
  });
}
