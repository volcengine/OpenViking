import {
  DEFAULT_ACCOUNT_ID,
  DEFAULT_USER_ID,
} from '#/features/connection/lib/admin-options'
import type { ConnectionDraft } from '#/features/connection/lib/types'
import { probeStudioConnection } from '#/shared/api/admin'
import { resolveApiKeyAuth } from '#/shared/api/auth'
import { resolveConfiguredServiceProvider } from '#/shared/lib/studio-service'
import type { ServerMode } from '#/shared/types/auth'

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
  allowManagement = true,
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
      !allowManagement ||
      resolveConfiguredServiceProvider(
        connection.baseUrl,
        connection.serviceSelection,
      ) !== 'opensource',
    dataRootUri: resolveConnectionDataRoot(connection),
    serverMode,
  })
}
