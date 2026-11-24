// @vitest-environment jsdom
import {
  AppConnectionProvider,
  useAppConnection,
} from '#/features/connection/hooks/use-app-connection'
import type { ConnectionProfiles } from '#/features/connection/lib/profiles'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  path: '/home',
  navigate: vi.fn(),
  requests: [] as { path: string; key?: string }[],
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
  useRouterState: () => mocks.path,
}))
vi.mock('#/features/connection/lib/credential-storage', () => ({
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
}))
vi.mock('#/features/connection/lib/health', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  fetchServerHealth: async (
    _url: string,
    headers: Record<string, string> = {},
  ) => {
    const key = headers['X-API-Key']
    mocks.requests.push({ path: mocks.path, key })
    return {
      status: 'ok',
      version: 'test',
      auth_mode: 'api_key',
      ...(key === 'root'
        ? { role: 'root', account_id: 'default', user_id: 'default' }
        : key === 'admin-a'
          ? { role: 'admin', account_id: 'a', user_id: 'manager' }
          : key
            ? {
                role: 'user',
                account_id: key === 'bob-key' ? 'b' : 'a',
                user_id: key === 'bob-key' ? 'bob' : 'alice',
              }
            : {}),
    }
  },
}))
const base = {
  serviceSelection: 'opensource' as const,
  baseUrl: 'http://ov.test',
  accountId: 'a',
  userId: 'alice',
  apiKey: 'alice-key',
  adminApiKey: 'root',
}
let api!: ReturnType<typeof useAppConnection>
function Capture() {
  api = useAppConnection()
  return null
}
const client = new QueryClient({
  defaultOptions: { queries: { retry: false } },
})
const tree = () => (
  <QueryClientProvider client={client}>
    <AppConnectionProvider>
      <Capture />
    </AppConnectionProvider>
  </QueryClientProvider>
)
async function settled(role: string) {
  await waitFor(() => expect(api.serverMode).toBe('api_key'))
  await waitFor(() => expect(api.isConnectionRoleLoading).toBe(false))
  await waitFor(() => expect(api.connectionRole).toBe(role))
}
beforeEach(() => {
  mocks.path = '/home'
  mocks.navigate.mockReset()
  mocks.requests = []
  localStorage.clear()
})
afterEach(() => {
  cleanup()
  client.clear()
  localStorage.clear()
})
function seed(extra: ConnectionProfiles['profiles'] = [], connection = base) {
  localStorage.setItem(
    'ov_console_connection_profiles',
    JSON.stringify({
      activeId: 'main',
      profiles: [{ id: 'main', name: 'OV', connection }, ...extra],
    }),
  )
}
it('keeps a user workspace while Root manages another Account and restores both surfaces after reload', async () => {
  seed()
  const view = render(tree())
  await settled('user')
  mocks.path = '/admin/users'
  view.rerender(tree())
  await settled('root')
  await act(() => api.switchManagementAccount('b', { preserveProfile: true }))
  await waitFor(() => expect(api.connection.accountId).toBe('b'))
  expect(api.connection.apiKey).toBe('')
  await act(() =>
    api.switchIdentity({
      accountId: 'b',
      userId: 'bob',
      apiKey: 'bob-key',
      preserveProfile: true,
      rememberWorkbench: false,
    }),
  )
  expect(api.connection).toMatchObject({
    accountId: 'b',
    userId: 'bob',
    apiKey: 'bob-key',
  })
  mocks.path = '/home'
  view.rerender(tree())
  await settled('user')
  expect(api.connection).toMatchObject(base)
  mocks.path = '/admin/users'
  view.rerender(tree())
  await settled('root')
  expect(api.connection.accountId).toBe('b')
  view.unmount()
  mocks.path = '/home'
  render(tree())
  await settled('user')
  expect(api.connection).toMatchObject(base)
  expect(
    mocks.requests
      .filter((request) => !request.path.startsWith('/admin'))
      .some((request) => request.key === 'root'),
  ).toBe(false)
})
it('pins an Account Admin to its own management Account without replacing a different saved data user', async () => {
  const paired = {
    ...base,
    adminApiKey: 'admin-a',
    accountId: 'b',
    userId: 'bob',
    apiKey: 'bob-key',
  }
  seed([], paired)
  const view = render(tree())
  await settled('user')
  mocks.path = '/admin/users'
  view.rerender(tree())
  await settled('admin')
  expect(api.connection).toMatchObject({
    accountId: 'a',
    userId: 'manager',
    apiKey: 'admin-a',
  })
  mocks.path = '/home'
  view.rerender(tree())
  await settled('user')
  expect(api.connection).toMatchObject(paired)
})
it('leaves administration when a new connection only grants ordinary data access', async () => {
  seed([
    {
      id: 'user',
      name: 'Member',
      connection: {
        ...base,
        apiKey: 'bob-key',
        accountId: 'b',
        userId: 'bob',
        adminApiKey: '',
      },
    },
  ])
  mocks.path = '/admin/users'
  render(tree())
  await settled('root')
  act(() => api.activateProfile('user'))
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith({ replace: true, to: '/home' }),
  )
})
it('leaves the data workbench for a Root-only connection without using Root for a data request', async () => {
  seed([
    {
      id: 'root',
      name: 'Root',
      connection: { ...base, apiKey: '', userId: '' },
    },
  ])
  render(tree())
  await settled('user')
  act(() => api.activateProfile('root'))
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith({
      replace: true,
      to: '/admin',
    }),
  )
  expect(mocks.requests.some((request) => request.key === 'root')).toBe(false)
})

it.each(['root', 'admin-a'])(
  'attaches %s from a User workbench without probing it until administration and restores the User on return',
  async (key) => {
    const original = {
      ...base,
      adminApiKey: '',
      accountId: 'b',
      userId: 'bob',
      apiKey: 'bob-key',
    }
    seed([], original)
    const view = render(tree())
    await settled('user')
    act(() => api.saveConnection({ ...api.connection, adminApiKey: key }))
    await settled('user')
    expect(api.connection).toMatchObject({ ...original, adminApiKey: key })
    expect(
      mocks.requests
        .filter((request) => !request.path.startsWith('/admin'))
        .some((request) => request.key === key),
    ).toBe(false)
    mocks.path = '/admin/users'
    view.rerender(tree())
    await settled(key === 'root' ? 'root' : 'admin')
    if (key === 'admin-a') expect(api.connection.accountId).toBe('a')
    mocks.path = '/home'
    view.rerender(tree())
    await settled('user')
    expect(api.connection).toMatchObject({ ...original, adminApiKey: key })
  },
)
