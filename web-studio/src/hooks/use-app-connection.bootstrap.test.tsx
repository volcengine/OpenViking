// @vitest-environment jsdom
import { createServer } from 'node:http'
import { once } from 'node:events'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'

import { AppConnectionProvider, useAppConnection } from './use-app-connection'
import type { ConnectionDraft } from './use-app-connection'
import * as health from './use-server-mode'
import { ovClient } from '#/lib/ov-client'

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => '/home',
}))

const queryClients: Array<QueryClient> = []
afterEach(() => {
  cleanup()
  for (const client of queryClients.splice(0)) client.clear()
  localStorage.removeItem('ov_console_connection')
  vi.restoreAllMocks()
})

function Identity() {
  const { connectionRole, isConnectionRoleLoading, serverMode } =
    useAppConnection()
  return (
    <output data-testid="identity">
      {serverMode}:{isConnectionRoleLoading ? 'loading' : connectionRole}
    </output>
  )
}

it.each(['', 'root-key'])(
  'detects trusted mode on cold startup and resolves identity only with a key: %s',
  async (rootKey) => {
    const observed: Array<
      [string | undefined, string | undefined, string | undefined]
    > = []
    const server = createServer((req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*')
      res.setHeader(
        'Access-Control-Allow-Headers',
        'X-OpenViking-Account, X-OpenViking-User, X-API-Key',
      )
      if (req.method === 'OPTIONS') {
        res.end()
        return
      }
      const account = req.headers['x-openviking-account'] as string | undefined
      const user = req.headers['x-openviking-user'] as string | undefined
      const apiKey = req.headers['x-api-key'] as string | undefined
      observed.push([account, user, apiKey])
      res.setHeader('Content-Type', 'application/json')
      if (account && user && apiKey !== 'root-key') {
        res.statusCode = 401
        res.end(JSON.stringify({ error: { code: 'UNAUTHENTICATED' } }))
        return
      }
      res.end(
        JSON.stringify({
          status: 'ok',
          auth_mode: 'trusted',
          root_api_key_required: true,
          ...(account && user && apiKey === 'root-key'
            ? { account_id: account, user_id: user, role: 'user' }
            : {}),
        }),
      )
    })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const address = server.address() as { port: number }
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    localStorage.setItem(
      'ov_console_connection',
      JSON.stringify({
        baseUrl: `http://127.0.0.1:${address.port}`,
        accountId: 'account-a',
        userId: 'alice',
        apiKey: '',
        adminApiKey: rootKey,
      }),
    )
    try {
      render(
        <QueryClientProvider client={queryClient}>
          <AppConnectionProvider>
            <Identity />
          </AppConnectionProvider>
        </QueryClientProvider>,
      )
      await waitFor(() =>
        expect(screen.getByTestId('identity').textContent).toBe(
          rootKey ? 'trusted:user' : 'trusted:unknown',
        ),
      )
      if (rootKey) {
        expect(observed).toContainEqual(['account-a', 'alice', rootKey])
      } else {
        expect(observed).toEqual([[undefined, undefined, undefined]])
      }
    } finally {
      cleanup()
      queryClient.clear()
      localStorage.removeItem('ov_console_connection')
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  },
)

const storedConnection: ConnectionDraft = {
  baseUrl: 'http://old.test',
  accountId: 'legacy-account',
  userId: 'claude',
  apiKey: '',
  adminApiKey: '',
}
const devProbe = {
  mode: 'dev' as const,
  data: { role: 'root', account_id: 'default', user_id: 'default' },
}

function renderConnection(connection = storedConnection) {
  let current: ReturnType<typeof useAppConnection>
  function Snapshot() {
    current = useAppConnection()
    return null
  }
  const queryClient = new QueryClient()
  queryClients.push(queryClient)
  localStorage.setItem('ov_console_connection', JSON.stringify(connection))
  render(
    <QueryClientProvider client={queryClient}>
      <AppConnectionProvider>
        <Snapshot />
      </AppConnectionProvider>
    </QueryClientProvider>,
  )
  return { current: () => current!, queryClient }
}

function expectConnection(
  current: ReturnType<typeof useAppConnection>,
  expected: ConnectionDraft,
) {
  expect(current.connection).toEqual(expected)
  expect(ovClient.getOptions().baseUrl).toBe(expected.baseUrl)
  expect(ovClient.getConnection()).toMatchObject({
    accountId: expected.accountId,
    userId: expected.userId,
    apiKey: expected.apiKey,
    adminApiKey: expected.adminApiKey,
    identityHeaders: current.serverMode === 'trusted',
  })
  expect(
    JSON.parse(localStorage.getItem('ov_console_connection') || '{}'),
  ).toEqual(expected)
}

it.each([
  ['default', 'default', false],
  ['server-team', 'server-user', true],
  ['', '', false],
] as const)(
  'synchronizes the dev identity returned by the server: %s/%s',
  async (accountId, userId, keyed) => {
    const data = accountId
      ? { role: 'root', account_id: accountId, user_id: userId }
      : {}
    const probe = vi
      .spyOn(health, 'detectServerConnection')
      .mockResolvedValue({ mode: 'dev', data })
    const connection = {
      ...storedConnection,
      apiKey: keyed ? 'data-key' : '',
      adminApiKey: keyed ? 'control-key' : '',
    }
    const { current, queryClient } = renderConnection(connection)
    queryClient.setQueryData(['legacy-data'], 'old-user-data')
    const expected = accountId
      ? { ...connection, accountId, userId }
      : connection
    await waitFor(() => {
      expect(current().serverMode).toBe('dev')
      expectConnection(current(), expected)
    })
    expect(queryClient.getQueryData(['legacy-data'])).toBe(
      accountId ? undefined : 'old-user-data',
    )
    expect(probe).toHaveBeenCalledWith(
      connection.baseUrl,
      keyed
        ? {
            'X-API-Key': 'control-key',
            'X-OpenViking-Account': 'legacy-account',
            'X-OpenViking-User': 'claude',
          }
        : {},
    )
    expect(probe).toHaveBeenCalledWith(
      connection.baseUrl,
      keyed ? { 'X-API-Key': 'data-key' } : {},
    )
  },
)

it.each(['trusted', 'api_key'] as const)(
  'preserves the requested identity when switching from dev to %s',
  async (mode) => {
    const probe = vi
      .spyOn(health, 'detectServerConnection')
      .mockImplementation(async (url) =>
        url === storedConnection.baseUrl ? devProbe : { mode, data: {} },
      )
    vi.spyOn(health, 'fetchServerHealth').mockResolvedValue({
      role: 'user',
      account_id: 'tenant-b',
      user_id: 'alice',
    })
    const { current } = renderConnection()
    await waitFor(() => expect(current().serverMode).toBe('dev'))
    const target = {
      ...storedConnection,
      baseUrl: 'http://target.test',
      accountId: 'tenant-b',
      userId: 'alice',
      apiKey: 'target-key',
    }
    act(() => current().saveConnection(target))
    expect(current().serverMode).toBe('checking')
    expectConnection(current(), target)
    await waitFor(() => {
      expect(current().serverMode).toBe(mode)
      expect(current().isConnectionRoleLoading).toBe(false)
    })
    expectConnection(current(), target)
    expect(probe).toHaveBeenCalledWith(target.baseUrl, {
      'X-API-Key': 'target-key',
      'X-OpenViking-Account': 'tenant-b',
      'X-OpenViking-User': 'alice',
    })
  },
)

it.each(['discovery', 'data', 'unchanged'] as const)(
  'keeps the saved connection when a dev %s response completes in the same batch',
  async (phase) => {
    let release!: () => void
    let waiting = false
    const pending = new Promise<typeof devProbe>((resolve) => {
      release = () => resolve(devProbe)
    })
    vi.spyOn(health, 'detectServerConnection').mockImplementation(
      (url, headers) => {
        if (
          url === storedConnection.baseUrl &&
          (phase !== 'data' || !headers?.['X-OpenViking-Account'])
        ) {
          waiting = true
          return pending
        }
        return Promise.resolve(
          url === storedConnection.baseUrl
            ? devProbe
            : { mode: 'trusted', data: {} },
        )
      },
    )
    const initial = {
      ...storedConnection,
      apiKey: phase === 'data' ? 'old-key' : '',
    }
    const { current } = renderConnection(initial)
    await waitFor(() => expect(waiting).toBe(true))
    const target =
      phase === 'unchanged'
        ? initial
        : {
            ...initial,
            baseUrl: 'http://target.test',
            accountId: 'tenant-b',
            userId: 'alice',
            apiKey: '',
          }
    await act(async () => {
      current().saveConnection(target)
      release()
      await pending
      // Run the response continuation before React flushes the save batch.
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
    const expected =
      phase === 'unchanged'
        ? { ...initial, accountId: 'default', userId: 'default' }
        : target
    await waitFor(() => {
      expect(current().serverMode).toBe(
        phase === 'unchanged' ? 'dev' : 'trusted',
      )
      expectConnection(current(), expected)
    })
  },
)
