// @vitest-environment jsdom
import {
  canOpenManagement,
  hasStudioAccountScope,
  readPreferredMode,
  resolveStudioEntry,
  savePreferredMode,
} from '#/features/connection/lib/studio-mode'
import { afterEach, expect, it } from 'vitest'

const connection = {
  baseUrl: 'http://service.test',
  accountId: 'team',
  userId: 'alice',
  apiKey: 'data-key',
  adminApiKey: 'root-key',
}
afterEach(() => localStorage.clear())
it('honors the trusted workbench preference without a User Key', () => {
  expect(
    resolveStudioEntry({
      connection: { ...connection, apiKey: '' },
      provider: 'opensource',
      canManage: true,
      preferredMode: 'workbench',
      serverMode: 'trusted',
    }),
  ).toBe('/home')
})
it.each(['/admin/configuration/server', '/admin/configuration/server/'])(
  'omits Account scope on global server configuration route %s',
  (path) => {
    expect(hasStudioAccountScope(path)).toBe(false)
  },
)
it.each([
  '/settings',
  '/admin/settings',
  '/connections/new',
  '/connections/user-key',
  '/admin/connections/profile-one',
  '/admin/users',
  '/admin/vikingbot',
  '/admin/configuration',
  '/filesystem',
])(
  'retains Account scope across connection settings and account pages: %s',
  (path) => {
    expect(hasStudioAccountScope(path)).toBe(true)
  },
)
it.each(['user', 'unknown'] as const)(
  'does not grant management to %s despite a saved control credential',
  (role) => {
    expect(
      canOpenManagement({
        connection,
        role,
        provider: 'opensource',
        serverMode: 'api_key',
        isRoleLoading: false,
      }),
    ).toBe(false)
  },
)
it('never grants hosted connections open source administration', () => {
  expect(
    canOpenManagement({
      connection,
      role: 'root',
      provider: 'volcengine',
      serverMode: 'api_key',
      isRoleLoading: false,
    }),
  ).toBe(false)
})
it('entry preferences cannot grant management or make a Root-only connection a data identity', () => {
  expect(
    resolveStudioEntry({
      connection,
      provider: 'opensource',
      canManage: false,
      preferredMode: 'management',
    }),
  ).toBe('/home')
  expect(
    resolveStudioEntry({
      connection: { ...connection, apiKey: '' },
      provider: 'opensource',
      canManage: true,
      preferredMode: 'workbench',
    }),
  ).toBe('/admin')
})
it('isolates optional entry preferences by service and account', () => {
  savePreferredMode(connection, 'workbench')
  expect(readPreferredMode(connection)).toBe('workbench')
  expect(readPreferredMode({ ...connection, accountId: 'other' })).toBeNull()
  expect(
    readPreferredMode({ ...connection, baseUrl: 'http://other.test' }),
  ).toBeNull()
})

it('keeps shared operational pages in the preceding mode without affecting business routes', async () => {
  const { resolveStudioMode, connectionSettingsPath } =
    await import('#/features/connection/lib/studio-mode')
  for (const path of ['/oauth/verify']) {
    expect(resolveStudioMode(path, 'management')).toBe('management')
    expect(connectionSettingsPath(path, 'management')).toBe('/admin/settings')
    expect(resolveStudioMode(path, 'workbench')).toBe('workbench')
  }
  expect(resolveStudioMode('/home', 'management')).toBe('workbench')
  for (const path of [
    '/admin/users',
    '/admin/configuration',
    '/admin/gateway',
    '/admin/gateway/profiles/new',
  ]) {
    expect(resolveStudioMode(path, 'workbench')).toBe('management')
    expect(connectionSettingsPath(path, 'workbench')).toBe('/admin/settings')
  }
})
it('isolates remembered mode by profile and defaults a Root-only profile to management', async () => {
  const { readRememberedStudioMode, rememberStudioMode } =
    await import('#/features/connection/lib/studio-mode')
  sessionStorage.clear()
  expect(readRememberedStudioMode('root', 'management')).toBe('management')
  rememberStudioMode('root', 'management')
  expect(readRememberedStudioMode('root')).toBe('management')
  expect(readRememberedStudioMode('ordinary-user')).toBe('workbench')
  sessionStorage.clear()
})

it.each([
  ['', '', false, '/home'],
  ['user', '', false, '/home'],
  ['admin', 'admin', true, '/home'],
  ['', 'root', true, '/admin'],
  ['user', 'root', true, '/home'],
  ['user', 'admin', true, '/home'],
  ['admin', 'root', true, '/home'],
] as const)(
  'uses a consistent automatic entry for data=%s control=%s',
  (apiKey, adminApiKey, canManage, expected) => {
    expect(
      resolveStudioEntry({
        connection: { ...connection, apiKey, adminApiKey },
        provider: 'opensource',
        canManage,
        preferredMode: null,
        serverMode: 'api_key',
      }),
    ).toBe(expected)
  },
)
it('honors explicit administration preference while development defaults to data access without a Key', () => {
  expect(
    resolveStudioEntry({
      connection,
      provider: 'opensource',
      canManage: true,
      preferredMode: 'management',
      serverMode: 'api_key',
    }),
  ).toBe('/admin')
  expect(
    resolveStudioEntry({
      connection: { ...connection, apiKey: '', adminApiKey: '' },
      provider: 'opensource',
      canManage: true,
      preferredMode: null,
      serverMode: 'dev',
    }),
  ).toBe('/home')
})

it('retains a connection entry preference while management and workbench use different Accounts', () => { savePreferredMode(connection, 'management', 'profile-1'); expect(readPreferredMode({ ...connection, accountId: 'other-account' }, 'profile-1')).toBe('management'); expect(readPreferredMode(connection, 'profile-2')).toBeNull() })
