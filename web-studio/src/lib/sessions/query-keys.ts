import type {
  ConnectionDraft,
  ConnectionRole,
} from '#/hooks/use-app-connection'

export const SESSIONS_KEY = ['sessions'] as const

export type SessionScopeKey = {
  accountId: string
  baseUrl: string
  keyHash: string
  keySource: 'api' | 'admin' | 'none'
  role: ConnectionRole
  userId: string
}

function hashSecret(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(36)
}

export function getSessionScopeKey(
  connection: ConnectionDraft,
  connectionRole: ConnectionRole,
): SessionScopeKey {
  const sessionKey = connection.apiKey || connection.adminApiKey
  return {
    accountId: connection.accountId,
    baseUrl: connection.baseUrl,
    keyHash: sessionKey ? hashSecret(sessionKey) : 'none',
    keySource: connection.apiKey
      ? 'api'
      : connection.adminApiKey
        ? 'admin'
        : 'none',
    role: connectionRole,
    userId: connection.userId,
  }
}

export function getSessionsQueryKey(scope: SessionScopeKey) {
  return [...SESSIONS_KEY, scope] as const
}

export function getSessionQueryKey(
  scope: SessionScopeKey,
  sessionId: string | undefined,
) {
  return [...SESSIONS_KEY, scope, sessionId] as const
}

export function getSessionMessagesQueryKey(
  scope: SessionScopeKey,
  sessionId: string,
) {
  return [...SESSIONS_KEY, scope, sessionId, 'messages'] as const
}
