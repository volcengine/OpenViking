import { DEFAULT_ACCOUNT_ID } from './admin-options'

import type { ConnectionDraft, ConnectionRole } from './types'
import type { ServerMode } from '#/shared/types/auth'

export function adminConsoleHeaders(
  connection: ConnectionDraft,
  role: ConnectionRole,
  serverMode: ServerMode,
): Record<string, string> | null {
  const apiKey = connection.adminApiKey.trim()
  if (!apiKey || !connection.accountId || (role !== 'root' && role !== 'admin'))
    return null

  if (serverMode === 'trusted') {
    if (!connection.userId) return null
    return { 'X-API-Key': apiKey, 'X-OpenViking-Role': 'admin' }
  }

  // An unbound Root key resolves to the default account in API-key mode.
  if (
    serverMode !== 'api_key' ||
    (role === 'root' && connection.accountId !== DEFAULT_ACCOUNT_ID)
  )
    return null

  return { 'X-API-Key': apiKey }
}
