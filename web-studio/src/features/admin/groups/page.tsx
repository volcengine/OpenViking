import type { ReactNode } from 'react'
import { UserGroups } from '#/features/admin/groups/components/user-groups'
import { UserManagementPanel } from '#/features/admin/users/page'
import { UserSpaceSwitcher } from '#/features/admin/users/components/user-space-switcher'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { resolveStudioManagementCapabilities } from '#/features/connection/lib/studio-permissions'

export function UserGroupsRoute({
  navigation,
}: { navigation?: ReactNode } = {}) {
  const { connection, connectionRole, isConnectionRoleLoading, serverMode } =
    useAppConnection()
  const { canManageAccounts, canManageUsers } =
    resolveStudioManagementCapabilities({
      hasControlCredential: Boolean(connection.adminApiKey.trim()),
      isRoleLoading: isConnectionRoleLoading,
      role: connectionRole,
      serverMode,
    })
  if (!canManageUsers) return <UserManagementPanel />
  return (
    <UserGroups
      navigation={
        <>
          <UserSpaceSwitcher canManageAccounts={canManageAccounts} />
          {navigation}
        </>
      }
      connection={{
        accountId: connection.accountId,
        apiKey: connection.adminApiKey,
        baseUrl: connection.baseUrl,
        userId: connection.userId,
      }}
    />
  )
}
