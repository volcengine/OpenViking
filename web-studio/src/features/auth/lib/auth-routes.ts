import { AuthMethod } from '#/platform/studio-contract'
import { StudioEntry } from '#/platform/entry/paths'

export const AUTH_PATHS = [
  StudioEntry.Login,
  StudioEntry.EmailLogin,
  StudioEntry.ApiKeyLogin,
  StudioEntry.ForgotPassword,
  StudioEntry.ResetPassword,
  StudioEntry.Invite,
] as const

export type AuthPath = (typeof AUTH_PATHS)[number]
export type AuthPage = AuthMethod | 'welcome' | 'forgot' | 'reset' | 'invite'

export function isAuthPath(path: string): path is AuthPath {
  return AUTH_PATHS.some((candidate) => candidate === path)
}

export function resolveAuthPage(
  path: AuthPath,
  defaultPage: AuthMethod | 'welcome',
): AuthPage {
  switch (path) {
    case StudioEntry.Login:
      return defaultPage
    case StudioEntry.EmailLogin:
      return AuthMethod.Email
    case StudioEntry.ApiKeyLogin:
      return AuthMethod.ApiKey
    case StudioEntry.ForgotPassword:
      return 'forgot'
    case StudioEntry.ResetPassword:
      return 'reset'
    case StudioEntry.Invite:
      return 'invite'
  }
}

declare module '@tanstack/react-router' {
  interface HistoryState {
    studioAuthEmail?: string
  }
}

export function isValidEmail(email: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())
}

export function isValidPassword(password: string): boolean {
  return (
    password.length >= 8 &&
    /[a-z]/i.test(password) &&
    /[0-9]/.test(password) &&
    /[\p{P}\p{S}]/u.test(password)
  )
}
