import type { ConnectionDraft } from '#/features/connection/lib/types'
import { createApiKeyHeaders, resolveApiKeyAuth } from '#/shared/api/auth'
import type { ServerMode } from '#/shared/types/auth'

export function createConnectionHealthHeaders(
  connection: ConnectionDraft,
  credential: 'control' | 'data' = 'control',
  serverMode?: ServerMode,
): Record<string, string> {
  const headers: Record<string, string> = {}
  const auth = resolveApiKeyAuth(
    connection.baseUrl,
    connection.serviceSelection,
  )
  const apiKey =
    auth === 'bearer'
      ? connection.apiKey
      : serverMode === 'trusted'
        ? connection.adminApiKey || connection.apiKey
        : credential === 'data'
          ? connection.apiKey
          : connection.adminApiKey || connection.apiKey
  if (!apiKey) return headers
  Object.assign(headers, createApiKeyHeaders(apiKey, auth))
  const isHosted = auth === 'bearer'
  const assertedIdentity = !(
    serverMode === 'trusted' && credential === 'control'
  )
  if (!isHosted && assertedIdentity && connection.accountId) {
    headers['X-OpenViking-Account'] = connection.accountId
  }
  if (!isHosted && assertedIdentity && connection.userId) {
    headers['X-OpenViking-User'] = connection.userId
  }
  return headers
}
