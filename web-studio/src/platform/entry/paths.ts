export const StudioEntry = {
  Login: '/login',
  EmailLogin: '/login/email',
  ApiKeyLogin: '/login/api-key',
  ForgotPassword: '/forgot-password',
  ResetPassword: '/reset-password',
  Invite: '/invite',
  Home: '/home',
  Filesystem: '/filesystem',
  Management: '/admin',
  Gateway: '/admin/gateway',
} as const
export type WorkbenchEntry =
  | typeof StudioEntry.Home
  | typeof StudioEntry.Filesystem

export function normalizeStudioPath(pathname: string): string {
  return pathname.replace(/\/+$/, '') || '/'
}

export function isManagementPath(pathname: string): boolean {
  const path = normalizeStudioPath(pathname)
  return path === '/admin' || path.startsWith('/admin/')
}

export function isConnectionSettingsPath(pathname: string): boolean {
  const path = normalizeStudioPath(pathname)
  return (
    path === '/settings' ||
    path === '/admin/settings' ||
    path.startsWith('/connections/') ||
    path.startsWith('/admin/connections/')
  )
}

export function isSharedStudioPath(pathname: string): boolean {
  return normalizeStudioPath(pathname) === '/oauth/verify'
}
