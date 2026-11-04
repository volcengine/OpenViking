import {
  ConnectionRole,
  ServiceProvider,
  ServerAuthMode,
  StudioSurface,
} from '../studio-contract'
import type { ServerMode, StudioMode } from '../studio-contract'
import { supportsNativeManagement } from '../deployment/experience'

export type StudioManagementCapabilities = {
  canManageAccounts: boolean
  canManageUsers: boolean
}

export function resolveStudioManagementCapabilities({
  serviceProvider = ServiceProvider.OpenSource,
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
    !supportsNativeManagement(serviceProvider) ||
    isRoleLoading ||
    serverMode === ServerAuthMode.Development ||
    !hasControlCredential
  ) {
    return {
      canManageAccounts: false,
      canManageUsers: false,
    }
  }

  return {
    canManageAccounts: role === ConnectionRole.Root,
    canManageUsers:
      role === ConnectionRole.Root || role === ConnectionRole.Admin,
  }
}

type ManagementInput = Parameters<typeof resolveStudioManagementCapabilities>[0]

export function canManageStudioGateway(input: ManagementInput): boolean {
  return (
    supportsNativeManagement(
      input.serviceProvider ?? ServiceProvider.OpenSource,
    ) &&
    (input.serverMode === ServerAuthMode.Development ||
      input.serverMode === ServerAuthMode.Trusted ||
      (input.serverMode === ServerAuthMode.ApiKey &&
        !input.isRoleLoading &&
        input.role !== ConnectionRole.Unknown))
  )
}

export function canEditStudioConfiguration(input: ManagementInput): boolean {
  if (
    !supportsNativeManagement(
      input.serviceProvider ?? ServiceProvider.OpenSource,
    )
  )
    return false
  if (input.serverMode === ServerAuthMode.Development)
    return !input.isRoleLoading && input.role === ConnectionRole.Root
  return (
    (input.serverMode === ServerAuthMode.ApiKey ||
      input.serverMode === ServerAuthMode.Trusted) &&
    resolveStudioManagementCapabilities(input).canManageAccounts
  )
}

// Exposing an entry must not trigger a control-key probe or switch identities.
export function canShowManagementEntry(
  input: ManagementInput & { surface: StudioMode },
): boolean {
  if (
    !supportsNativeManagement(
      input.serviceProvider ?? ServiceProvider.OpenSource,
    )
  )
    return false
  if (input.surface === StudioSurface.Workbench)
    return (
      !input.isRoleLoading &&
      (input.serverMode === ServerAuthMode.Trusted ||
        input.serverMode === ServerAuthMode.Development ||
        input.role === ConnectionRole.Admin) &&
      (input.serverMode === ServerAuthMode.Development ||
        input.hasControlCredential)
    )
  return canOpenManagementSurface(input)
}

export function canOpenManagementSurface(input: ManagementInput): boolean {
  if (
    !supportsNativeManagement(
      input.serviceProvider ?? ServiceProvider.OpenSource,
    )
  )
    return false
  if (input.serverMode === ServerAuthMode.Development)
    return !input.isRoleLoading && input.role === ConnectionRole.Root
  return resolveStudioManagementCapabilities(input).canManageUsers
}
