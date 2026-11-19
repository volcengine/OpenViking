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

export function splitSourceLines(value: string): string[] {
  return [
    ...new Set(
      value
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter(Boolean),
    ),
  ];
}

export function parseSourceUris(value: string): string[] | undefined {
  const result = new Set<string>();
  for (const line of splitSourceLines(value)) {
    const uri = parseCompileUri(line);
    if (!uri) return;
    result.add(uri);
  }
  return [...result];
}

/** Compile requires a child of resource and memory namespaces; skill roots follow their own rules. */
export function isCompileDataNamespaceRoot(uri: string) {
  const [scope, ...path] = uri.slice(scheme.length).split('/');
  if (scope === 'resources') return path.length === 0;
  if (scope !== '~' && scope !== 'user') return false;
  const personal = scope === 'user' ? path.slice(1) : path;
  const content = personal[0] === 'peers' ? personal.slice(2) : personal;
  return (
    content.length === 1 &&
    (content[0] === 'resources' || content[0] === 'memories')
  );
}

export function compileTargetError(uri: string) {
  const [scope, ...path] = uri.slice(scheme.length).split('/');
  if (scope === 'system') return 'targetReadOnly';
  if (isCompileDataNamespaceRoot(uri)) return 'targetNamespaceRoot';
  const content = scope === 'user' ? path.slice(1) : path;
  const skillNamespace =
    ['agent', 'user', '~'].includes(scope ?? '') && content[0] === 'skills';
  if (skillNamespace && content.length !== 1) return 'targetSkillNamespace';
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
