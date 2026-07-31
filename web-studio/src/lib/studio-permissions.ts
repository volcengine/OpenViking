import type { ConnectionRole } from '#/hooks/use-app-connection'
import type { ServerMode } from '#/hooks/use-server-mode'

export type StudioManagementCapabilities = {
  canManageAccounts: boolean
  canManageUsers: boolean
}

export function resolveStudioManagementCapabilities({
  hasControlCredential,
  isRoleLoading,
  role,
  serverMode,
}: {
  hasControlCredential: boolean
  isRoleLoading: boolean
  role: ConnectionRole
  serverMode: ServerMode
}): StudioManagementCapabilities {
  if (isRoleLoading || serverMode === 'dev' || !hasControlCredential) {
    return {
      canManageAccounts: false,
      canManageUsers: false,
    }
  }

  return {
    canManageAccounts: role === 'root',
    canManageUsers: role === 'root' || role === 'admin',
  }
}
