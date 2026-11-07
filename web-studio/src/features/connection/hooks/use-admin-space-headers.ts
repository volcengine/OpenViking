import { adminSpaceCredentialQuery } from '#/features/connection/lib/admin-space-credential'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import * as React from 'react'

import { useAppConnection } from './use-app-connection'
import { adminConsoleHeaders } from '#/features/connection/lib/admin-console-headers'
import { createConnectionRoleProbeKey } from '#/features/connection/lib/connection-runtime'
import { normalizeOvClientError } from '#/shared/api/ov-client/index'

/** Resolve a tenant credential without replacing the Root control credential. */
export function useAdminSpaceHeaders(management: boolean) {
  const { connection, connectionRole, isConnectionRoleLoading, serverMode } =
    useAppConnection()
  const queryClient = useQueryClient()
  const needsAccountKey =
    management && serverMode === 'api_key' && connectionRole === 'root'
  const probeKey = createConnectionRoleProbeKey(connection, serverMode)
  const recoveryScope = React.useRef<string | null>(null)
  const queryKey = React.useMemo(
    () => ['admin-space-credential', probeKey],
    [probeKey],
  )
  const credential = useQuery({
    ...adminSpaceCredentialQuery(connection, serverMode),
    enabled: needsAccountKey && !isConnectionRoleLoading,
  })
  const headers = !management
    ? undefined
    : needsAccountKey
      ? credential.isError
        ? null
        : (credential.data?.headers ?? null)
      : adminConsoleHeaders(connection, connectionRole, serverMode)
  const scopeKey = createConnectionRoleProbeKey(
    needsAccountKey
      ? {
          ...connection,
          userId: credential.data?.userId ?? '',
          apiKey: JSON.stringify(headers),
        }
      : connection,
    serverMode,
  )

  // A tenant key can be rotated or revoked while Root remains valid.
  React.useEffect(() => {
    if (!needsAccountKey || !credential.data) return
    return queryClient.getQueryCache().subscribe((event) => {
      if (event.type !== 'updated' || event.action.type !== 'error') return
      if (event.query.meta?.adminSpaceScope !== scopeKey) return
      const code = normalizeOvClientError(event.query.state.error).statusCode
      if (
        (code === 401 || code === 403) &&
        recoveryScope.current !== scopeKey
      ) {
        recoveryScope.current = scopeKey
        queryClient.setQueryData(queryKey, null)
        void queryClient.invalidateQueries({ queryKey })
      }
    })
  }, [needsAccountKey, credential.data, queryKey, scopeKey, queryClient])

  return {
    headers,
    scopeKey,
    unavailableKey: needsAccountKey
      ? credential.isError
        ? ('appShell:adminNavigation.accountAdminLookupFailed' as const)
        : ('appShell:adminNavigation.accountAdminUnavailable' as const)
      : ('appShell:adminNavigation.scopeUnavailable' as const),
    isLoading:
      Boolean(isConnectionRoleLoading) ||
      (needsAccountKey && credential.isPending),
  }
}
