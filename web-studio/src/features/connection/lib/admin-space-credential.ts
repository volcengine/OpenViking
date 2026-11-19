import { createConnectionRoleProbeKey } from './connection-runtime'
import { fetchServerHealth, readServerIdentity } from './health'
import type { ConnectionDraft } from './types'
import type { ServerMode } from '#/shared/types/auth'
import { createApiKeyHeaders, resolveApiKeyAuth } from '#/shared/api/auth'
import { fetchAdminUsers } from '#/shared/api/admin'
import { normalizeOvClientError } from '#/shared/api/ov-client/index'
import { queryOptions } from '@tanstack/react-query'

export function adminSpaceCredentialQuery(
  connection: ConnectionDraft,
  serverMode: ServerMode,
) {
  return queryOptions({
    queryKey: [
      'admin-space-credential',
      createConnectionRoleProbeKey(connection, serverMode),
    ],
    staleTime: 30_000,
    gcTime: 300_000,
    retry: false,
    queryFn: async ({ signal }) => {
      const users = await fetchAdminUsers(
        { ...connection, apiKey: connection.adminApiKey },
        connection.accountId,
        true,
      )
      const auth = resolveApiKeyAuth(
        connection.baseUrl,
        connection.serviceSelection,
      )
      for (const user of users) {
        if (signal.aborted) throw signal.reason
        if (user.role !== 'admin' || !user.apiKey) continue
        const headers = createApiKeyHeaders(user.apiKey, auth)
        try {
          const health = await fetchServerHealth(
            connection.baseUrl,
            headers,
            signal,
          )
          const identity = readServerIdentity(health)
          if (
            health.role === 'admin' &&
            identity.accountId === connection.accountId &&
            identity.userId === user.userId
          )
            return {
              headers,
              apiKey: user.apiKey,
              userId: user.userId,
              rootOnlyUserIds: users
                .filter((entry) => entry.role === 'global')
                .map((entry) => entry.userId),
            }
        } catch (error) {
          const code = normalizeOvClientError(error).statusCode
          if (code !== 401 && code !== 403) throw error
        }
      }
      return null
    },
  })
}
