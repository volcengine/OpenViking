import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { isVerifiedAdminDelegation } from '#/features/connection/lib/health'
import { resolveStudioManagementCapabilities } from '#/features/connection/lib/studio-permissions'
import { createResourceAclApi } from '#/features/resource-acl/lib/resource-acl'
import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'

export function useAclManagement(load = true) {
  const {
    connection,
    connectionRole,
    isConnectionRoleLoading,
    serverMode,
    identityScopeKey,
    switchIdentity,
    studioMode,
    serverHealth,
  } = useAppConnection()
  const { canManageUsers } = resolveStudioManagementCapabilities({
    hasControlCredential: Boolean(connection.adminApiKey.trim()),
    isRoleLoading: isConnectionRoleLoading,
    role: connectionRole,
    serverMode,
  })
  const allowed =
    studioMode === 'management' &&
    canManageUsers &&
    Boolean(connection.accountId) &&
    serverMode !== 'checking' &&
    serverMode !== 'offline'
  // An account admin's control key is also a valid tenant data credential.
  // Keep the regular browsing identity unchanged while managing ACLs.
  const useAccountAdminCredential =
    serverMode === 'api_key' &&
    connectionRole === 'admin' &&
    !connection.adminDelegation
  const delegationVerified =
    serverMode === 'api_key' &&
    isVerifiedAdminDelegation(
      serverHealth,
      connection.userId,
      connection.accountId,
    )
  const api = useMemo(
    () =>
      createResourceAclApi(
        connection,
        serverMode === 'trusted',
        useAccountAdminCredential,
        delegationVerified,
      ),
    [connection, serverMode, useAccountAdminCredential, delegationVerified],
  )
  const settingsKey = [
    'account-acl',
    connection.baseUrl,
    connection.accountId,
    connection.adminApiKey,
  ]
  const settings = useQuery({
    queryKey: settingsKey,
    queryFn: () => api.getEnabled(),
    enabled: allowed && load,
    retry: false,
  })
  const adminConnection = {
    accountId: connection.accountId,
    userId: connection.userId,
    baseUrl: connection.baseUrl,
    apiKey: connection.adminApiKey,
  }
  return {
    api,
    serverMode,
    switchIdentity,
    allowed,
    hasDataIdentity:
      serverMode !== 'api_key' ||
      useAccountAdminCredential ||
      Boolean(connection.apiKey) ||
      Boolean(connection.adminDelegation),
    settings,
    settingsKey,
    adminConnection,
    identityScopeKey,
    aclIdentityScopeKey: `${identityScopeKey}:${useAccountAdminCredential ? 'account-admin' : 'data-user'}`,
    useAccountAdminCredential,
    connection,
  }
}
