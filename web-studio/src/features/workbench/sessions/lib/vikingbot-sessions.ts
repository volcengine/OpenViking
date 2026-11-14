import { createRandomUuid } from '#/shared/lib/browser-crypto'

// Persist ownership in the session ID: the generic session API has no source field.
// Legacy IDs are accepted only from the identity-scoped Filesystem Agent history.
const WEB_SESSION_PREFIX = 'vikingbot-web-'
const WEB_SESSION_UUID =
  '[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}'
const WEB_SESSION_PATTERN = new RegExp(
  `^${WEB_SESSION_PREFIX}${WEB_SESSION_UUID}$`,
  'i',
)
// The Bot OpenAPI scopes its session key, then portable_path_component escapes
// the separators and adds a digest. Older Studio IDs also carry a scope prefix.
const WEB_STORAGE_SESSION_PATTERN = new RegExp(
  `^cli__default__[0-9a-f]{24}%3A(${WEB_SESSION_PREFIX}(?:[0-9a-f]{8}-)?${WEB_SESSION_UUID})` +
    '(?:%3A[0-9a-f]{32})?~[0-9a-f]{16}$',
  'i',
)

export function createVikingBotWebSessionId() {
  return `${WEB_SESSION_PREFIX}${createRandomUuid()}`
}

export function isVikingBotWebSession(
  session: { session_id: string },
  legacyFilesystemIds: readonly string[] = [],
) {
  return (
    WEB_SESSION_PATTERN.test(session.session_id) ||
    legacyFilesystemIds.includes(session.session_id)
  )
}

/** Hide a Bot storage entry only when its Studio conversation is in the list. */
export function filterDuplicateVikingBotSessions<
  T extends { session_id: string },
>(sessions: readonly T[]): T[] {
  const ids = new Set(sessions.map((session) => session.session_id))
  return sessions.filter(({ session_id }) => {
    const webId = WEB_STORAGE_SESSION_PATTERN.exec(session_id)?.[1]
    return !webId || !ids.has(webId)
  })
}
