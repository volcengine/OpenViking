import type { ConnectionRole } from '#/features/connection/lib/types'
import type { ServiceProvider } from '#/shared/lib/studio-service'
import type { ServerMode } from '#/shared/types/auth'

export type StudioManagementCapabilities = {
  canManageAccounts: boolean
  canManageUsers: boolean
}

export function resolveStudioManagementCapabilities({
  serviceProvider = 'opensource',
  hasControlCredential,
  isRoleLoading,
  role,
  serverMode,
}: {
  serviceProvider?: ServiceProvider
  hasControlCredential: boolean
  isRoleLoading: boolean
  role: ConnectionRole
  serverMode: ServerMode
}): StudioManagementCapabilities {
  if (
    serviceProvider !== 'opensource' ||
    isRoleLoading ||
    serverMode === 'dev' ||
    !hasControlCredential
  ) {
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

type ManagementInput = Parameters<typeof resolveStudioManagementCapabilities>[0]

export function canManageStudioGateway(input: ManagementInput): boolean {
  return (
    (input.serviceProvider ?? 'opensource') === 'opensource' &&
    (input.serverMode === 'dev' ||
      input.serverMode === 'trusted' ||
      (input.serverMode === 'api_key' &&
        !input.isRoleLoading &&
        input.role !== 'unknown'))
  )
}

export function canEditStudioConfiguration(input: ManagementInput): boolean {
  if (input.serverMode === 'dev')
    return (
      !input.isRoleLoading &&
      input.role === 'root' &&
      (input.serviceProvider ?? 'opensource') === 'opensource'
    )
  return (
    (input.serverMode === 'api_key' || input.serverMode === 'trusted') &&
    resolveStudioManagementCapabilities(input).canManageAccounts
  )
}
