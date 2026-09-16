import { createRandomUuid } from '#/lib/browser-crypto'

// Persist ownership in the session ID: the generic session API has no source field.
// Legacy IDs are accepted only from the identity-scoped Playground Agent history.
const WEB_SESSION_PREFIX = 'vikingbot-web-'
const WEB_SESSION_PATTERN =
  /^vikingbot-web-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

export function createVikingBotWebSessionId() {
  return `${WEB_SESSION_PREFIX}${createRandomUuid()}`
}

export function isVikingBotWebSession(
  session: { session_id: string },
  legacyPlaygroundIds: readonly string[] = [],
) {
  return (
    WEB_SESSION_PATTERN.test(session.session_id) ||
    legacyPlaygroundIds.includes(session.session_id)
  )
}
