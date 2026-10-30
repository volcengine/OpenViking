import type { ConnectionDraft, ConnectionRole } from './connection/types'
import type { ServerMode } from './connection/health'
import { resolveStudioManagementCapabilities } from './studio-permissions'
import type { ServiceProvider } from './studio-service'

export type StudioMode = 'management' | 'workbench'
export function isManagementPath(pathname: string): boolean {
  return pathname === '/admin' || pathname.startsWith('/admin/')
}
export function isConnectionSettingsPath(pathname: string): boolean {
  return (
    pathname === '/settings' ||
    pathname === '/admin/settings' ||
    pathname === '/connections/new' ||
    pathname.startsWith('/connections/') ||
    pathname === '/admin/connections/new' ||
    pathname.startsWith('/admin/connections/')
  )
}
export function isSharedStudioPath(pathname: string): boolean {
  return pathname === '/oauth/verify'
}
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
  return resolveStudioManagementCapabilities({
    serviceProvider: input.provider,
    hasControlCredential: Boolean(input.connection.adminApiKey.trim()),
    role: input.role,
    isRoleLoading: input.isRoleLoading,
    serverMode: input.serverMode,
  }).canManageUsers
}

function preferenceKey(connection: ConnectionDraft): string {
  return `ov_studio_entry:${JSON.stringify([connection.baseUrl.trim().replace(/\/+$/, ''), connection.accountId])}`
}
export function readPreferredMode(
  connection: ConnectionDraft,
): StudioMode | null {
  try {
    const mode = localStorage.getItem(preferenceKey(connection))
    return mode === 'management' || mode === 'workbench' ? mode : null
  } catch {
    return null
  }
}
export function savePreferredMode(
  connection: ConnectionDraft,
  mode: StudioMode,
): void {
  try {
    localStorage.setItem(preferenceKey(connection), mode)
  } catch {
    // Preferences are optional when storage is unavailable.
  }
}
export function resolveStudioEntry({
  canManage,
  connection,
  provider,
  preferredMode,
}: {
  canManage: boolean
  connection: ConnectionDraft
  provider: ServiceProvider
  preferredMode: StudioMode | null
}): '/admin' | '/home' | '/filesystem' {
  if (canManage && (preferredMode !== 'workbench' || !connection.apiKey))
    return '/admin'
  return provider === 'opensource' ? '/home' : '/filesystem'
}

export function newConnectionPath(
  settingsPath: '/settings' | '/admin/settings',
): '/connections/new' | '/admin/connections/new' {
  return settingsPath === '/admin/settings'
    ? '/admin/connections/new'
    : '/connections/new'
}
