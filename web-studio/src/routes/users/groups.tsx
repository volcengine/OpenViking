import { createFileRoute } from '@tanstack/react-router'
import { useAppConnection } from '#/hooks/use-app-connection'
import { resolveStudioManagementCapabilities } from '#/lib/studio-permissions'
import { UserGroups } from './-components/user-groups'
import { UserManagementPanel } from './route'

export const Route = createFileRoute('/users/groups')({
  component: UserGroupsRoute,
})

function UserGroupsRoute() {
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
