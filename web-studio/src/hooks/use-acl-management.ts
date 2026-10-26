import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAppConnection } from './use-app-connection'
import { resolveStudioManagementCapabilities } from '#/lib/studio-permissions'
import { createResourceAclApi } from '#/lib/resource-acl'

export function useAclManagement(load = true) {
  const {
    connection,
    connectionRole,
    isConnectionRoleLoading,
    serverMode,
    identityScopeKey,
    switchIdentity,
  } = useAppConnection()
  const { canManageUsers } = resolveStudioManagementCapabilities({
    hasControlCredential: Boolean(connection.adminApiKey.trim()),
    isRoleLoading: isConnectionRoleLoading,
    role: connectionRole,
    serverMode,
  })
  const allowed =
    canManageUsers &&
    Boolean(connection.accountId) &&
    serverMode !== 'checking' &&
    serverMode !== 'offline'
  // An account admin's control key is also a valid tenant data credential.
  // Keep the regular browsing identity unchanged while managing ACLs.
  const useAccountAdminCredential =
    serverMode === 'api_key' && connectionRole === 'admin'
  const api = useMemo(
    () =>
      createResourceAclApi(
        connection,
        serverMode === 'trusted',
        useAccountAdminCredential,
      ),
    [connection, serverMode, useAccountAdminCredential],
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
    settings,
    settingsKey,
    adminConnection,
    identityScopeKey,
    aclIdentityScopeKey: `${identityScopeKey}:${useAccountAdminCredential ? 'account-admin' : 'data-user'}`,
    useAccountAdminCredential,
    connection,
  }
}
