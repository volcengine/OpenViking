import { isManagementPath, isSharedStudioPath } from '#/platform/entry/paths'
import { canOpenManagementSurface } from '#/platform/access/management'
import type { StudioMode, ServiceProvider } from '#/platform/studio-contract'
import type {
  ConnectionDraft,
  ConnectionRole,
} from '#/features/connection/lib/types'
import type { ServerMode } from '#/shared/types/auth'
import { resolveStudioEntry as resolveEntry } from '#/platform/entry/resolve-entry'
import { resolveConnectionEntryAccess } from './connection-identity'

export type { StudioMode } from '#/platform/studio-contract'

/** Compatibility for old setup consumers; new routes consume accessContext. */
export function resolveStudioEntry(input: {
  connection: ConnectionDraft
  provider: ServiceProvider
  canManage: boolean
  preferredMode: StudioMode | null
  serverMode?: ServerMode
  role?: ConnectionRole
}) {
  return resolveEntry({
    access: resolveConnectionEntryAccess(input),
    provider: input.provider,
    preferredMode: input.preferredMode,
  })
}

export {
  isManagementPath,
  isConnectionSettingsPath,
  isSharedStudioPath,
} from '#/platform/entry/paths'
export function hasStudioAccountScope(pathname: string): boolean {
  const path = pathname.replace(/\/+$/, '')
  return path !== '/admin/configuration/server'
}
export function resolveStudioMode(
  pathname: string,
  remembered: StudioMode = 'workbench',
): StudioMode {
  if (isManagementPath(pathname)) return 'management'
  return isSharedStudioPath(pathname) ? remembered : 'workbench'
}
export function readRememberedStudioMode(
  profileId: string,
  fallback: StudioMode = 'workbench',
): StudioMode {
  try {
    const stored = sessionStorage.getItem(`ov_studio_surface:${profileId}`)
    return stored === 'management' || stored === 'workbench' ? stored : fallback
  } catch {
    return fallback
  }
}
export function rememberStudioMode(profileId: string, mode: StudioMode): void {
  try {
    sessionStorage.setItem(`ov_studio_surface:${profileId}`, mode)
  } catch {
    /* Mode history is optional. */
  }
}
export function connectionSettingsPath(
  pathname: string,
  remembered: StudioMode = 'workbench',
): '/settings' | '/admin/settings' {
  return resolveStudioMode(pathname, remembered) === 'management'
    ? '/admin/settings'
    : '/settings'
}
export function canOpenManagement(input: {
  provider: ServiceProvider
  connection: ConnectionDraft
  role: ConnectionRole
  isRoleLoading: boolean
  serverMode: ServerMode
}): boolean {
  return canOpenManagementSurface({
    serviceProvider: input.provider,
    hasControlCredential: Boolean(input.connection.adminApiKey.trim()),
    isRoleLoading: input.isRoleLoading,
    role: input.role,
    serverMode: input.serverMode,
  })
}

function preferenceKey(connection: ConnectionDraft): string {
  return `ov_studio_entry:${JSON.stringify([connection.baseUrl.trim().replace(/\/+$/, ''), connection.accountId])}`
}
export function readPreferredMode(
  connection: ConnectionDraft,
  profileId?: string,
): StudioMode | null {
  try {
    const mode =
      (profileId
        ? localStorage.getItem(`ov_studio_entry_profile:${profileId}`)
        : null) ?? localStorage.getItem(preferenceKey(connection))
    return mode === 'management' || mode === 'workbench' ? mode : null
  } catch {
    return null
  }
}
export function savePreferredMode(
  connection: ConnectionDraft,
  mode: StudioMode,
  profileId?: string,
): void {
  try {
    localStorage.setItem(
      profileId
        ? `ov_studio_entry_profile:${profileId}`
        : preferenceKey(connection),
      mode,
    )
  } catch {
    // Preferences are optional when storage is unavailable.
  }
}
export function newConnectionPath(
  settingsPath: '/settings' | '/admin/settings',
): '/connections/new' | '/admin/connections/new' {
  return settingsPath === '/admin/settings'
    ? '/admin/connections/new'
    : '/connections/new'
}
