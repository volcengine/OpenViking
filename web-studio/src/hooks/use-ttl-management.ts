import { useMemo } from 'react'
import {
  createConnectionRoleProbeKey,
  useAppConnection,
} from './use-app-connection'
import { createTtlApi } from '#/lib/ttl'
import { resolveStudioManagementCapabilities } from '#/lib/studio-permissions'

export function useTtlManagement() {
  const { connection, connectionRole, isConnectionRoleLoading, serverMode } =
    useAppConnection()
  const { canManageUsers } = resolveStudioManagementCapabilities({
    hasControlCredential: Boolean(connection.adminApiKey.trim()),
    isRoleLoading: isConnectionRoleLoading,
    role: connectionRole,
    serverMode,
  })
  const allowed =
    Boolean(connection.accountId) &&
    canManageUsers &&
    serverMode !== 'checking' &&
    serverMode !== 'offline'
  const api = useMemo(
    () =>
      createTtlApi(
        connection,
        serverMode === 'trusted',
        serverMode === 'api_key' && connectionRole === 'admin',
      ),
    [connection, serverMode, connectionRole],
  )
  const scopeKey = createConnectionRoleProbeKey(connection, serverMode)
  return {
    api,
    allowed,
    scopeKey,
    connection,
    configKey: ['ttl-config', scopeKey],
  }
}
