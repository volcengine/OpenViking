import i18n from '#/i18n/core'

const VIKING_URI = /^viking:\/\/([a-zA-Z0-9._-]+)(?:\/(.*))?$/

export function normalizeVikingUri(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, '')
  const match = VIKING_URI.exec(trimmed)
  if (!match || !match[1]) {
    throw new Error(i18n.t('arranger:validation.validVikingUri'))
  }

  const segments = (match[2] || '').split('/').filter(Boolean)
  if (segments.some((segment) => segment === '.' || segment === '..')) {
    throw new Error(i18n.t('arranger:validation.noRelativePathSegments'))
  }
  return `viking://${match[1]}${segments.length ? `/${segments.join('/')}` : ''}`
}

export function isWithinRoot(uri: string, rootUri: string): boolean {
  const uriValue = normalizeVikingUri(uri)
  const rootValue = normalizeVikingUri(rootUri)
  return uriValue === rootValue || uriValue.startsWith(`${rootValue}/`)
}

export function toggleExpandedUri(
  expanded: ReadonlySet<string>,
  uri: string,
): Set<string> {
  const target = normalizeVikingUri(uri)
  const isExpanded = [...expanded].some(
    (current) => normalizeVikingUri(current) === target,
  )
  if (!isExpanded) return new Set([...expanded, uri])

  return new Set(
    [...expanded].filter((current) => !isWithinRoot(current, target)),
  )
}

export function joinVikingUri(parent: string, name: string): string {
  const cleanName = name.trim()
  if (
    !cleanName ||
    cleanName.includes('/') ||
    cleanName === '.' ||
    cleanName === '..'
  ) {
    throw new Error(i18n.t('arranger:validation.singlePathSegment'))
  }
  return `${normalizeVikingUri(parent)}/${cleanName}`
}

export function parentVikingUri(uri: string): string {
  const normalized = normalizeVikingUri(uri)
  const schemeIndex = normalized.indexOf('://')
  const slash = normalized.lastIndexOf('/')
  if (slash <= schemeIndex + 2) {
    return normalized
  }
  return normalized.slice(0, slash)
}

export function nameFromVikingUri(uri: string): string {
  return normalizeVikingUri(uri).split('/').pop() || ''
}

export function mcpServerNameFromToolUri(uri: string): string | null {
  try {
    const match =
      /^viking:\/\/(?:agent|user\/[^/]+)\/tools\/([^/]+)\/[^/]+\.json$/.exec(
        normalizeVikingUri(uri),
      )
    return match?.[1] || null
  } catch {
    return null
  }
}

export function resolveMountNodeUri(
  parentUri: string,
  name: string,
  existingUri?: string,
): string {
  return existingUri
    ? normalizeVikingUri(existingUri)
    : joinVikingUri(parentUri, name)
}

export function assertMutationBoundary(
  source: string,
  target: string | undefined,
  rootUri: string,
): void {
  const root = normalizeVikingUri(rootUri)
  if (normalizeVikingUri(source) === root) {
    throw new Error(i18n.t('arranger:validation.rootImmutable'))
  }
  if (target && !isWithinRoot(target, root)) {
    throw new Error(i18n.t('arranger:validation.destinationOutsideRoot'))
  }
  if (
    target &&
    (normalizeVikingUri(target) === normalizeVikingUri(source) ||
      normalizeVikingUri(target).startsWith(`${normalizeVikingUri(source)}/`))
  ) {
    throw new Error(i18n.t('arranger:validation.destinationInsideSource'))
  }
}
