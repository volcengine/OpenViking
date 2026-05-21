import { normalizeDirUri } from '#/lib/viking-uri'

import { KNOWN_VIKING_SCOPES } from '../-constants/retrieval'
import type { RetrievalScope } from '../-types/retrieval'

export function normalizeScopeInput(value: string): string | undefined {
  const trimmed = value.trim()
  if (!trimmed || trimmed === '/' || trimmed === 'viking://') {
    return undefined
  }

  if (trimmed.startsWith('viking://')) {
    return normalizeDirUri(trimmed)
  }

  const path = trimmed.replace(/^\/+/, '')
  if (!path) {
    return undefined
  }

  const [scope] = path.split('/')
  const scopedPath = KNOWN_VIKING_SCOPES.has(scope) ? path : `resources/${path}`
  return normalizeDirUri(`viking://${scopedPath}`)
}

export function resolveScopeTargetUri(
  scope: RetrievalScope,
  customPathInput: string,
): string | undefined {
  if (scope === 'all') {
    return undefined
  }

  if (scope === 'resources') {
    return 'viking://resources/'
  }

  return normalizeScopeInput(customPathInput)
}
