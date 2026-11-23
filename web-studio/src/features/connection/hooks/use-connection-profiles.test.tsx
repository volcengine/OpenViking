// @vitest-environment jsdom
import {
  AppConnectionProvider,
  useAppConnection,
} from '#/features/connection/hooks/use-app-connection'
import type * as ServerModeModule from '#/features/connection/lib/health'
import { PROFILES_STORAGE_KEY } from '#/features/connection/lib/profiles'
import * as adminApi from '#/shared/api/admin'
import { ovClient } from '#/shared/api/ov-client/index'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const { health, navigate, routing } = vi.hoisted(() => ({
  health: vi.fn(),
  routing: { pathname: '/settings' },
  navigate: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useRouterState: () => routing.pathname,
}))
vi.mock('#/features/connection/lib/health', async (original) => ({
  ...(await original<typeof ServerModeModule>()),
  fetchServerHealth: health,
}))
afterEach(() => {
  cleanup()
  routing.pathname = '/settings'
  localStorage.clear()
  health.mockReset()
  vi.restoreAllMocks()
})
const source = {
  baseUrl: 'http://10.0.0.1:1933',
  serviceSelection: 'opensource' as const,
  accountId: 'default',
  userId: 'alice',
  apiKey: 'source-key',
  adminApiKey: '',
}
function Controls() {
  const value = useAppConnection()
  return (
    <>
      <output>
        {JSON.stringify({
          id: value.activeProfileId,
          mode: value.serverMode,
          health: value.serverHealth,
          connection: value.connection,
        })}
      </output>
      <button
        onClick={() =>
          value.saveProfile({
            id: 'cloud',
            name: 'Cloud',
            connection: {
              ...source,
              baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
              serviceSelection: 'volcengine',
              apiKey: 'cloud-key',
            },
          })
        }
      >
        add
      </button>
      <button onClick={() => value.activateProfile('cloud')}>cloud</button>
      <button onClick={() => value.activateProfile('default')}>source</button>
      <button onClick={() => value.deleteProfile('default')}>
        delete active
      </button>
    </>
  )
}
function mount(client: QueryClient) {
  return render(
    <QueryClientProvider client={client}>
      <AppConnectionProvider>
        <Controls />
      </AppConnectionProvider>
    </QueryClientProvider>,
  )
}
it('saves without activation, switches credentials, clears cache and restores the enabled profile on reload', async () => {
  localStorage.setItem('ov_console_connection', JSON.stringify(source))
  health.mockImplementation(async (_url, headers) => ({
    auth_mode: 'api_key',
    version: '0.5.1',
    role: 'user',
    account_id: 'default',
    user_id: headers.Authorization ? 'cloud-user' : 'alice',
  }))
  const client = new QueryClient()
  const view = mount(client)
  await waitFor(() =>
    expect(document.querySelector('output')!.textContent).toContain(
      '"mode":"api_key"',
    ),
  )
  fireEvent.click(screen.getByText('delete active'))
  expect(
    JSON.parse(localStorage.getItem(PROFILES_STORAGE_KEY)!).profiles,
  ).toHaveLength(1)
  fireEvent.click(screen.getByText('add'))
  expect(ovClient.getOptions().apiKeyAuth).toBe('header')
  client.setQueryData(['old-files'], ['private-file'])
  fireEvent.click(screen.getByText('cloud'))
  expect(ovClient.getOptions().apiKeyAuth).toBe('bearer')
  expect(ovClient.getConnection().apiKey).toBe('cloud-key')
  expect(client.getQueryData(['old-files'])).toBeUndefined()
  await waitFor(() =>
    expect(document.querySelector('output')!.textContent).toContain(
      'cloud-user',
    ),
  )
  expect(health).toHaveBeenCalledWith(
    'https://api.vikingdb.cn-beijing.volces.com/openviking',
    { Authorization: 'Bearer cloud-key' },
    expect.any(AbortSignal),
  )
  view.unmount()
  mount(client)
  await waitFor(() =>
    expect(document.querySelector('output')!.textContent).toContain(
      '"id":"cloud"',
    ),
  )
  fireEvent.click(screen.getByText('source'))
  await waitFor(() =>
    expect(ovClient.getConnection().apiKey).toBe('source-key'),
  )
  expect(ovClient.getOptions().apiKeyAuth).toBe('header')
  client.clear()
})
it('ignores an old pending health response after another profile is enabled', async () => {
  localStorage.setItem('ov_console_connection', JSON.stringify(source))
  let resolveOld!: (value: unknown) => void
  health.mockImplementation((url) =>
    url === source.baseUrl
      ? new Promise((resolve) => {
          resolveOld = resolve
        })
      : Promise.resolve({ auth_mode: 'api_key', version: 'cloud' }),
  )
  const client = new QueryClient()
  mount(client)
  fireEvent.click(screen.getByText('add'))
  fireEvent.click(screen.getByText('cloud'))
  await waitFor(() =>
    expect(document.querySelector('output')!.textContent).toContain(
      '"version":"cloud"',
    ),
  )
  await act(async () =>
    resolveOld({
      auth_mode: 'trusted',
      version: 'old',
      role: 'user',
      account_id: 'old-account',
      user_id: 'old-user',
    }),
  )
  expect(document.querySelector('output')!.textContent).not.toContain(
    'old-user',
  )
  expect(document.querySelector('output')!.textContent).not.toContain(
    '"mode":"trusted"',
  )
  client.clear()
})

it('a pending identity switch must not overwrite a newly enabled profile', async () => {
  localStorage.setItem('ov_console_connection', JSON.stringify(source))
  let resolveSwitch!: (value: unknown) => void
  health.mockImplementation((_url, headers) =>
    headers['X-API-Key'] === 'bob-key'
      ? new Promise((resolve) => {
          resolveSwitch = resolve
        })
      : Promise.resolve({
          auth_mode: 'api_key',
          version: '0.5.1',
          role: 'user',
          account_id: 'default',
          user_id: headers.Authorization ? 'cloud-user' : 'alice',
        }),
  )
  let api!: ReturnType<typeof useAppConnection>
  function Capture() {
    api = useAppConnection()
    return null
  }
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <AppConnectionProvider>
        <Capture />
      </AppConnectionProvider>
    </QueryClientProvider>,
  )
  await waitFor(() => expect(api.serverMode).toBe('api_key'))
  let switching!: Promise<void | Error>
  await act(async () => {
    switching = api
      .switchIdentity({
        accountId: 'default',
        userId: 'bob',
        apiKey: 'bob-key',
      })
      .catch((error: Error) => error)
  })
  await act(async () => {
    api.saveProfile({
      id: 'cloud',
      name: 'Cloud',
      connection: {
        ...source,
        baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
        serviceSelection: 'volcengine',
        apiKey: 'cloud-key',
      },
    })
    api.activateProfile('cloud')
  })
  await waitFor(() => expect(api.connection.apiKey).toBe('cloud-key'))
  await act(async () => {
    resolveSwitch({
      auth_mode: 'api_key',
      role: 'user',
      account_id: 'default',
      user_id: 'bob',
    })
    await switching
  })
  expect(await switching).toBeInstanceOf(Error)
  expect(api.activeProfileId).toBe('cloud')
  expect(api.connection.apiKey).toBe('cloud-key')
  client.clear()
})

it('rejects an identity switch started by an obsolete profile callback', async () => {
  localStorage.setItem('ov_console_connection', JSON.stringify(source))
  health.mockImplementation(async (_url, headers) => ({
    auth_mode: 'api_key',
    version: '0.5.1',
    role: 'user',
    account_id: 'default',
    user_id: headers.Authorization ? 'cloud-user' : 'alice',
  }))
  let api!: ReturnType<typeof useAppConnection>
  function Capture() {
    api = useAppConnection()
    return null
  }
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <AppConnectionProvider>
        <Capture />
      </AppConnectionProvider>
    </QueryClientProvider>,
  )
  await waitFor(() => expect(api.serverMode).toBe('api_key'))
  const oldSwitchIdentity = api.switchIdentity
  await act(async () => {
    api.saveProfile({
      id: 'cloud',
      name: 'Cloud',
      connection: {
        ...source,
        baseUrl: 'https://api.vikingdb.cn-beijing.volces.com/openviking',
        serviceSelection: 'volcengine',
        apiKey: 'cloud-key',
      },
    })
    api.activateProfile('cloud')
  })
  await waitFor(() => expect(api.connection.apiKey).toBe('cloud-key'))
  await expect(
    oldSwitchIdentity({
      accountId: 'default',
      userId: 'bob',
      apiKey: 'bob-key',
    }),
  ).rejects.toMatchObject({ code: 'connectionChanged' })
  expect(api.activeProfileId).toBe('cloud')
  expect(api.connection.apiKey).toBe('cloud-key')
  const saved = JSON.parse(localStorage.getItem(PROFILES_STORAGE_KEY)!)
  expect(
    saved.profiles.find((profile: { id: string }) => profile.id === 'cloud')
      .connection.apiKey,
  ).toBe('cloud-key')
  expect(
    health.mock.calls.some(([, headers]) => headers['X-API-Key'] === 'bob-key'),
  ).toBe(false)
  client.clear()
})

it('keeps the selected connection profile stable while changing Account and User through scope controls', async () => {
  routing.pathname = '/admin/settings'
  vi.spyOn(adminApi, 'fetchAdminAccounts').mockResolvedValue([])
  localStorage.setItem(
    'ov_console_connection',
    JSON.stringify({ ...source, adminApiKey: 'root-key' }),
  )
  health.mockImplementation(async (_url, headers) => ({
    auth_mode: 'api_key',
    version: '0.5.1',
    role: headers['X-API-Key'] === 'root-key' ? 'root' : 'user',
    account_id: headers['X-API-Key'] === 'bob-key' ? 'team-b' : 'default',
    user_id: headers['X-API-Key'] === 'bob-key' ? 'bob' : 'alice',
  }))
  let api!: ReturnType<typeof useAppConnection>
  function Capture() {
    api = useAppConnection()
    return null
  }
  const client = new QueryClient()
  render(
    <QueryClientProvider client={client}>
      <AppConnectionProvider>
        <Capture />
      </AppConnectionProvider>
    </QueryClientProvider>,
  )
  await waitFor(() => expect(api.connectionRole).toBe('root'))
  const profileId = api.activeProfileId
  const profileName = api.profiles.find(
    (profile) => profile.id === profileId,
  )!.name
  await act(async () => {
    await api.switchManagementAccount('team-b', { preserveProfile: true })
  })
  expect(api.activeProfileId).toBe(profileId)
  expect(api.connection).toMatchObject({
    accountId: 'team-b',
    userId: '',
    apiKey: '',
    adminApiKey: 'root-key',
  })
  await waitFor(() => expect(api.connectionRole).toBe('root'))
  await act(async () => {
    await api.switchIdentity({
      accountId: 'team-b',
      userId: 'bob',
      apiKey: 'bob-key',
      preserveProfile: true,
    })
  })
  expect(api.activeProfileId).toBe(profileId)
  expect(api.profiles).toHaveLength(1)
  expect(api.profiles[0].name).toBe(profileName)
  expect(api.connection).toMatchObject({
    accountId: 'team-b',
    userId: 'bob',
    apiKey: 'bob-key',
    adminApiKey: 'root-key',
  })
  const saved = JSON.parse(localStorage.getItem(PROFILES_STORAGE_KEY)!)
  expect(saved.activeId).toBe(profileId)
  expect(saved.profiles[0].connection.userId).toBe('bob')
  client.clear()
})

// Persistence is tested separately with real Web Crypto in credential-storage.test.ts.
vi.mock('#/features/connection/lib/credential-storage', () => ({
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
  flushCredentialStorage: async () => {},
}))
