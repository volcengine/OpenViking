import { createRandomUuid } from '#/shared/lib/browser-crypto'

// Persist ownership in the session ID: the generic session API has no source field.
// Legacy IDs are accepted only from the identity-scoped Filesystem Agent history.
const WEB_SESSION_PREFIX = 'vikingbot-web-'
const BOT_OWNED_PREFIX = 'vikingbot-web-v2-'
const WEB_SESSION_PATTERN =
  /^vikingbot-web-(?:v2-)?[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function createVikingBotWebSessionId(
  botOwned = false,
  uuid = createRandomUuid(),
) {
  return `${botOwned ? BOT_OWNED_PREFIX : WEB_SESSION_PREFIX}${uuid}`
}

export function isBotOwnedWebSession(sessionId: string) {
  return (
    sessionId.startsWith(BOT_OWNED_PREFIX) &&
    WEB_SESSION_PATTERN.test(sessionId)
  )
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
