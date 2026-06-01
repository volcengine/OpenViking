export function fileNameFromUri(uri: string): string {
  const trimmed = uri.endsWith('/') ? uri.slice(0, -1) : uri
  const index = trimmed.lastIndexOf('/')
  if (index < 0) return trimmed
  return trimmed.slice(index + 1) || trimmed
}

export function normalizeDirUri(uri: string): string {
  const value = uri.trim()
  if (!value) {
    return 'viking://'
  }
  if (value === 'viking://') {
    return value
  }
  return value.endsWith('/') ? value : `${value}/`
}

export function normalizeFileUri(uri: string): string {
  const value = uri.trim()
  if (!value) {
    return 'viking://'
  }
  if (value === 'viking://') {
    return value
  }
  return value.endsWith('/') ? value.slice(0, -1) : value
}

export function parentUri(uri: string): string {
  const normalized = normalizeDirUri(uri)
  if (normalized === 'viking://') {
    return normalized
  }

  const body = normalized.slice('viking://'.length, -1)
  if (!body.includes('/')) {
    return 'viking://'
  }

  return `viking://${body.slice(0, body.lastIndexOf('/') + 1)}`
}

export function joinUri(baseUri: string, child: string): string {
  const raw = child.trim()
  if (!raw) {
    return normalizeDirUri(baseUri)
  }
  if (raw.startsWith('viking://')) {
    return raw
  }

  const normalizedBase = normalizeDirUri(baseUri)
  return `${normalizedBase}${raw.replace(/^\//, '')}`
}

/**
 * Matches `viking://` URIs inside arbitrary text. Stops at whitespace, quotes,
 * brackets and the punctuation that commonly trails a URI in prose. Shared so
 * the sessions view and the studio playground extract URIs identically.
 */
export const VIKING_URI_RE = /viking:\/\/[^\s,，。；;'"`<>()\]}\\]+/g

/**
 * Extract the first `viking://` URI from a string (or accept an already-clean
 * URI) and strip trailing punctuation that bleeds in from surrounding prose.
 */
export function cleanVikingUri(value: string): string {
  const match = value.match(VIKING_URI_RE)
  return (match?.[0] ?? value).trim().replace(/[\\，。；;,.]+$/u, '')
}
