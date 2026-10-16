import { probeStudioConnection } from '../admin'
import { DEFAULT_ACCOUNT_ID, DEFAULT_USER_ID } from '../admin-options'
import { resolveConfiguredServiceProvider } from '../studio-service'
import { resolveApiKeyAuth } from './auth'
import type { ConnectionDraft } from './types'
import type { ServerMode } from './health'

export function resolveConnectionDataRoot(
  connection: ConnectionDraft,
  scope: 'root' | 'user' = 'user',
): string {
  if (
    resolveConfiguredServiceProvider(
      connection.baseUrl,
      connection.serviceSelection,
    ) === 'volcengine'
  )
    return 'viking://~/'
  return scope === 'user' && connection.userId
    ? `viking://user/${connection.userId}/`
    : 'viking://'
}

/** Diagnostic probes intentionally report access states instead of blocking setup. */
export function probeConnection(
  connection: ConnectionDraft,
  serverMode: ServerMode,
) {
  return probeStudioConnection({
    ...connection,
    apiKeyAuth: resolveApiKeyAuth(
      connection.baseUrl,
      connection.serviceSelection,
    ),
    accountId: connection.accountId || DEFAULT_ACCOUNT_ID,
    userId: connection.userId || DEFAULT_USER_ID,
    skipAdmin:
      resolveConfiguredServiceProvider(
        connection.baseUrl,
        connection.serviceSelection,
      ) !== 'opensource',
    dataRootUri: resolveConnectionDataRoot(connection),
    serverMode,
  })
}
