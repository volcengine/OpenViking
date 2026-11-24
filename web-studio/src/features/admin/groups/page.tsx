import { UserGroups } from '#/features/admin/groups/components/user-groups'
import { UserManagementPanel } from '#/features/admin/users/page'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { resolveStudioManagementCapabilities } from '#/features/connection/lib/studio-permissions'

export function UserGroupsRoute() {
  const { connection, connectionRole, isConnectionRoleLoading, serverMode } =
    useAppConnection()
  const { canManageUsers } = resolveStudioManagementCapabilities({
    hasControlCredential: Boolean(connection.adminApiKey.trim()),
    isRoleLoading: isConnectionRoleLoading,
    role: connectionRole,
    serverMode,
  })
  if (!canManageUsers) return <UserManagementPanel />
  return (
    <UserGroups
      connection={{
        accountId: connection.accountId,
        apiKey: connection.adminApiKey,
        baseUrl: connection.baseUrl,
        userId: connection.userId,
      }}
    />
  )
}
