// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { once } from 'node:events'
import { useEffect, useState } from 'react'
import { createAdminClient } from '#/shared/api/admin'
import { ovClient } from '#/shared/api/ov-client/index'
import { createServer } from 'node:http'
import { afterEach, expect, it, vi } from 'vitest'

import {
  AppConnectionProvider,
  useAppConnection,
} from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { useAdminSpaceHeaders } from '#/features/connection/hooks/use-admin-space-headers'

const { navigate, routing } = vi.hoisted(() => ({
  navigate: vi.fn(),
  routing: { pathname: '/home' },
}))

it('keeps the verified Root shell and cache mounted when only the management Account changes', async () => {
  routing.pathname = '/admin/dashboard'
  let rootProbes = 0
  let mounts = 0
  const server = createServer((req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*')
    res.setHeader('Access-Control-Allow-Headers', '*')
    if (req.method === 'OPTIONS') {
      res.end()
      return
    }
    res.setHeader('Content-Type', 'application/json')
    const key = String(req.headers['x-api-key'] ?? '')
    const url = new URL(req.url!, 'http://test')
    if (url.pathname === '/health') {
      if (key === 'root-key') rootProbes += 1
      res.end(
        JSON.stringify({
          auth_mode: 'api_key',
          version: '0.5.4',
          account_id: key === 'second-key' ? 'second' : 'default',
          user_id: key === 'root-key' ? 'default' : 'admin',
          ...(key ? { role: key === 'root-key' ? 'root' : 'admin' } : {}),
        }),
      )
    } else {
      const account = url.pathname.includes('/second/') ? 'second' : 'default'
      res.end(
        JSON.stringify({
          status: 'ok',
          result: [
            {
              user_id: 'admin',
              role: 'admin',
              api_key: `${account}-key`,
            },
          ],
        }),
      )
    }
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address() as { port: number }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  localStorage.setItem(
    'ov_console_connection',
    JSON.stringify({
      baseUrl: `http://127.0.0.1:${address.port}`,
      accountId: 'default',
      userId: 'default',
      apiKey: '',
      adminApiKey: 'root-key',
    }),
  )
  function Content() {
    useEffect(() => {
      mounts += 1
    }, [])
    const { connection } = useAppConnection()
    const scoped = useAdminSpaceHeaders(true)
    return (
      <output data-testid="scoped-content">
        {connection.accountId}:{scoped.headers?.['X-API-Key']}
      </output>
    )
  }
  function Page() {
    const app = useAppConnection()
    const service = useStudioService()
    return (
      <>
        <button onClick={() => void app.switchManagementAccount('second')}>
          second
        </button>
        <button onClick={() => void app.switchManagementAccount('default')}>
          default
        </button>
        <button onClick={app.reconnect}>reconnect</button>
        {service.ready && !app.isConnectionRoleLoading ? (
          <Content />
        ) : (
          <span>checking</span>
        )}
      </>
    )
  }
  try {
    render(
      <QueryClientProvider client={client}>
        <AppConnectionProvider>
          <Page />
        </AppConnectionProvider>
      </QueryClientProvider>,
    )
    await waitFor(() =>
      expect(screen.getByTestId('scoped-content').textContent).toBe(
        'default:default-key',
      ),
    )
    const initialMounts = mounts
    const initialProbes = rootProbes
    client.setQueryData(['unrelated-data'], 'keep')
    fireEvent.click(screen.getByRole('button', { name: 'second', exact: true }))
    await waitFor(() =>
      expect(screen.getByTestId('scoped-content').textContent).toBe(
        'second:second-key',
      ),
    )
    expect(mounts).toBe(initialMounts)
    expect(rootProbes).toBe(initialProbes)
    expect(client.getQueryData(['unrelated-data'])).toBe('keep')
    fireEvent.click(
      screen.getByRole('button', { name: 'default', exact: true }),
    )
    await waitFor(() =>
      expect(screen.getByTestId('scoped-content').textContent).toBe(
        'default:default-key',
      ),
    )
    expect(mounts).toBe(initialMounts)
    expect(rootProbes).toBe(initialProbes)
    fireEvent.click(
      screen.getByRole('button', { name: 'reconnect', exact: true }),
    )
    await waitFor(() => expect(rootProbes).toBeGreaterThan(initialProbes))
    await waitFor(() =>
      expect(screen.getByTestId('scoped-content').textContent).toBe(
        'default:default-key',
      ),
    )
  } finally {
    cleanup()
    client.clear()
    localStorage.removeItem('ov_console_connection')
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useRouterState: () => routing.pathname,
}))

afterEach(() => {
  cleanup()
  navigate.mockReset()
  routing.pathname = '/home'
  localStorage.removeItem('ov_console_connection_profiles')
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
  'discovers trusted mode before authenticating the workbench with its configured key: %s',
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
      expect(observed[0]).toEqual([undefined, undefined, undefined])
      if (rootKey) {
        expect(observed).toContainEqual(['account-a', 'alice', rootKey])
      } else expect(observed).toHaveLength(1)
    } finally {
      cleanup()
      queryClient.clear()
      localStorage.removeItem('ov_console_connection')
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  },
)

function ConnectionState() {
  const { connection, connectionRole, isConnectionRoleLoading, serverMode } =
    useAppConnection()
  return (
    <output data-testid="connection-state">
      {JSON.stringify({
        mode: serverMode,
        role: isConnectionRoleLoading ? 'loading' : connectionRole,
        account: connection.accountId,
        user: connection.userId,
      })}
    </output>
  )
}

it.each([
  [401, '/home', '/settings'],
  [503, '/home', '/settings'],
  [401, '/admin/users', '/admin/settings'],
  [503, '/admin/users', '/admin/settings'],
] as const)(
  'opens connection recovery only for rejected credentials (HTTP %s at %s)',
  async (status, pathname, destination) => {
    routing.pathname = pathname
    const server = createServer((req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*')
      res.setHeader('Access-Control-Allow-Headers', '*')
      if (req.method === 'OPTIONS') {
        res.end()
        return
      }
      res.statusCode = status
      res.setHeader('Content-Type', 'application/json')
      res.end(
        JSON.stringify({
          error: { code: status === 401 ? 'UNAUTHENTICATED' : 'UNAVAILABLE' },
        }),
      )
    })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const address = server.address() as { port: number }
    const client = new QueryClient()
    localStorage.setItem(
      'ov_console_connection',
      JSON.stringify({
        baseUrl: `http://127.0.0.1:${address.port}`,
        accountId: 'default',
        userId: 'default',
        apiKey: 'revoked-user-key',
        adminApiKey: '',
      }),
    )
    try {
      render(
        <QueryClientProvider client={client}>
          <AppConnectionProvider>
            <ConnectionState />
          </AppConnectionProvider>
        </QueryClientProvider>,
      )
      await waitFor(() =>
        expect(
          JSON.parse(screen.getByTestId('connection-state').textContent).mode,
        ).toBe('offline'),
      )
      if (status === 401)
        expect(navigate).toHaveBeenCalledWith({ to: destination })
      else expect(navigate).not.toHaveBeenCalled()
    } finally {
      cleanup()
      client.clear()
      localStorage.removeItem('ov_console_connection')
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  },
)

it('keeps a valid data connection and its resolved identity when a separate control key has expired', async () => {
  const keys: unknown[] = []
  const server = createServer((req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*')
    res.setHeader('Access-Control-Allow-Headers', '*')
    if (req.method === 'OPTIONS') {
      res.end()
      return
    }
    const key = req.headers['x-api-key']
    keys.push(key)
    res.setHeader('Content-Type', 'application/json')
    res.statusCode = key === 'user-key' ? 200 : 401
    res.end(
      JSON.stringify(
        key === 'user-key'
          ? {
              auth_mode: 'api_key',
              version: '0.4.22',
              account_id: 'account-a',
              user_id: 'alice',
              role: 'user',
            }
          : {
              status: 'error',
              error: {
                code: 'UNAUTHENTICATED',
                message: 'Invalid control key',
              },
            },
      ),
    )
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address() as { port: number }
  const client = new QueryClient()
  localStorage.setItem(
    'ov_console_connection',
    JSON.stringify({
      baseUrl: `http://127.0.0.1:${address.port}`,
      accountId: 'default',
      userId: 'default',
      apiKey: 'user-key',
      adminApiKey: 'expired-control-key',
    }),
  )
  try {
    render(
      <QueryClientProvider client={client}>
        <AppConnectionProvider>
          <ConnectionState />
        </AppConnectionProvider>
      </QueryClientProvider>,
    )
    await waitFor(() =>
      expect(
        JSON.parse(screen.getByTestId('connection-state').textContent),
      ).toEqual({
        mode: 'api_key',
        role: 'user',
        account: 'account-a',
        user: 'alice',
      }),
    )
    expect(keys[0]).toBe('user-key')
    expect(keys).not.toContain('expired-control-key')
  } finally {
    cleanup()
    client.clear()
    localStorage.removeItem('ov_console_connection')
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})

function RecoveryState() {
  const {
    reconnect,
    connection,
    connectionRole,
    isConnectionRoleLoading,
    serverMode,
  } = useAppConnection()
  const service = useStudioService()
  return (
    <>
      <button onClick={reconnect}>Retry connection</button>
      <output data-testid="recovery-state">
        {JSON.stringify({
          mode: serverMode,
          role: isConnectionRoleLoading ? 'loading' : connectionRole,
          account: connection.accountId,
          user: connection.userId,
          provider: service.provider,
          version: service.version,
        })}
      </output>
    </>
  )
}

it('reconnects health, auth mode and identity together after a temporary outage without changing credentials', async () => {
  let healthy = false
  const server = createServer((req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*')
    res.setHeader('Access-Control-Allow-Headers', '*')
    if (req.method === 'OPTIONS') {
      res.end()
      return
    }
    res.setHeader('Content-Type', 'application/json')
    res.statusCode = healthy ? 200 : 503
    res.end(
      JSON.stringify(
        healthy
          ? {
              status: 'ok',
              auth_mode: 'api_key',
              version: '0.4.22',
              role: 'user',
              account_id: 'account-a',
              user_id: 'alice',
            }
          : {
              status: 'error',
              error: { code: 'UNAVAILABLE', message: 'Temporary outage' },
            },
      ),
    )
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const client = new QueryClient()
  localStorage.setItem(
    'ov_console_connection',
    JSON.stringify({
      baseUrl: `http://127.0.0.1:${(server.address() as { port: number }).port}`,
      accountId: 'default',
      userId: 'default',
      apiKey: 'user-key',
      adminApiKey: '',
    }),
  )
  try {
    render(
      <QueryClientProvider client={client}>
        <AppConnectionProvider>
          <RecoveryState />
        </AppConnectionProvider>
      </QueryClientProvider>,
    )
    const state = () =>
      JSON.parse(screen.getByTestId('recovery-state').textContent)
    await waitFor(() => expect(state().mode).toBe('offline'))
    expect(state().provider).toBe('unknown')
    client.setQueryData(['studio-feature', 'previous'], 'supported')
    healthy = true
    fireEvent.click(screen.getByRole('button', { name: 'Retry connection' }))
    await waitFor(() =>
      expect(state()).toEqual({
        mode: 'api_key',
        role: 'user',
        account: 'account-a',
        user: 'alice',
        provider: 'opensource',
        version: '0.4.22',
      }),
    )
    expect(client.getQueryData(['studio-feature', 'previous'])).toBeUndefined()
  } finally {
    cleanup()
    client.clear()
    localStorage.removeItem('ov_console_connection')
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})

it('exposes the resolved data user before a slow management probe completes', async () => {
  const pendingControl: Array<() => void> = []
  const readyUsers: string[] = []
  let controlReleased = false
  const server = createServer((req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*')
    res.setHeader('Access-Control-Allow-Headers', '*')
    if (req.method === 'OPTIONS') {
      res.end()
      return
    }
    res.setHeader('Content-Type', 'application/json')
    if (req.url !== '/health') {
      res.statusCode = 403
      res.end(JSON.stringify({ error: { code: 'FORBIDDEN' } }))
      return
    }
    if (req.headers['x-api-key'] === 'slow-control') {
      const reply = () =>
        res.end(
          JSON.stringify({
            auth_mode: 'api_key',
            version: '0.4.22',
            role: 'admin',
            account_id: 'management-account',
            user_id: 'manager',
          }),
        )
      if (controlReleased) reply()
      else pendingControl.push(reply)
      return
    }
    res.end(
      JSON.stringify({
        auth_mode: 'api_key',
        version: '0.4.22',
        role: 'user',
        account_id: 'account-a',
        user_id: 'alice',
      }),
    )
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address() as { port: number }
  const client = new QueryClient()
  localStorage.setItem(
    'ov_console_connection',
    JSON.stringify({
      baseUrl: `http://127.0.0.1:${address.port}`,
      accountId: 'default',
      userId: 'default',
      apiKey: 'user-key',
      adminApiKey: 'slow-control',
    }),
  )
  function WorkspaceIdentity() {
    const service = useStudioService()
    const { connection, isConnectionRoleLoading } = useAppConnection()
    if (service.ready) readyUsers.push(connection.userId)
    return (
      <output data-testid="workspace-identity">
        {JSON.stringify({
          ready: service.ready,
          user: connection.userId,
          managementPending: isConnectionRoleLoading,
        })}
      </output>
    )
  }
  try {
    render(
      <QueryClientProvider client={client}>
        <AppConnectionProvider>
          <WorkspaceIdentity />
        </AppConnectionProvider>
      </QueryClientProvider>,
    )
    await waitFor(() =>
      expect(
        JSON.parse(screen.getByTestId('workspace-identity').textContent),
      ).toEqual({ ready: true, user: 'alice', managementPending: true }),
    )
    expect(readyUsers.length).toBeGreaterThan(0)
    expect(readyUsers.every((user) => user === 'alice')).toBe(true)
    controlReleased = true
    for (const release of pendingControl.splice(0)) release()
    await waitFor(() =>
      expect(
        JSON.parse(screen.getByTestId('workspace-identity').textContent),
      ).toEqual({ ready: true, user: 'alice', managementPending: false }),
    )
    expect(readyUsers.every((user) => user === 'alice')).toBe(true)
  } finally {
    cleanup()
    for (const release of pendingControl) release()
    client.clear()
    localStorage.removeItem('ov_console_connection')
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})

// Persistence is tested separately with real Web Crypto in credential-storage.test.ts.
vi.mock('#/features/connection/lib/credential-storage', () => ({
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
  flushCredentialStorage: async () => {},
}))

it('prefers the verified Account Admin through both clients after Root connection bootstrap', async () => {
  routing.pathname = '/admin/users'
  const observed: Array<[string, unknown]> = []
  const server = createServer((req, res) => {
    res.setHeader('Access-Control-Allow-Origin', '*')
    res.setHeader('Access-Control-Allow-Headers', '*')
    if (req.method === 'OPTIONS') {
      res.end()
      return
    }
    const key = req.headers['x-api-key']
    const url = new URL(req.url!, 'http://test')
    observed.push([url.pathname, key])
    res.setHeader('Content-Type', 'application/json')
    if (url.pathname === '/health') {
      res.end(
        JSON.stringify({
          auth_mode: 'api_key',
          version: '0.4.22',
          account_id: 'default',
          user_id: key === 'space-admin' ? 'admin' : 'default',
          ...(key ? { role: key === 'space-admin' ? 'admin' : 'root' } : {}),
        }),
      )
    } else if (url.pathname === '/api/v1/admin/accounts/default/users') {
      res.end(
        JSON.stringify({
          status: 'ok',
          result: [{ user_id: 'admin', role: 'admin', api_key: 'space-admin' }],
        }),
      )
    } else {
      res.end(JSON.stringify({ status: 'ok', result: [] }))
    }
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address() as { port: number }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  localStorage.setItem(
    'ov_console_connection',
    JSON.stringify({
      baseUrl: `http://127.0.0.1:${address.port}`,
      accountId: 'default',
      userId: 'default',
      apiKey: '',
      adminApiKey: 'root-key',
    }),
  )
  function Requests() {
    const { connection, connectionRole, isConnectionRoleLoading } =
      useAppConnection()
    const [done, setDone] = useState(false)
    useEffect(() => {
      if (isConnectionRoleLoading || connectionRole !== 'root') return
      void Promise.all([
        createAdminClient({
          ...connection,
          apiKey: connection.adminApiKey,
        }).get({ url: '/api/v1/admin/accounts/default/groups' }),
        ovClient.client.get({
          url: '/api/v1/admin/accounts/default/configuration',
        }),
        ovClient.client.get({ url: '/api/v1/admin/configuration' }),
      ]).then(() => setDone(true))
    }, [connection, connectionRole, isConnectionRoleLoading])
    return <output data-testid="routed-requests">{String(done)}</output>
  }
  try {
    render(
      <QueryClientProvider client={client}>
        <AppConnectionProvider>
          <Requests />
        </AppConnectionProvider>
      </QueryClientProvider>,
    )
    await waitFor(() =>
      expect(screen.getByTestId('routed-requests').textContent).toBe('true'),
    )
    expect(observed).toContainEqual([
      '/api/v1/admin/accounts/default/groups',
      'space-admin',
    ])
    expect(observed).toContainEqual([
      '/api/v1/admin/accounts/default/configuration',
      'space-admin',
    ])
    expect(observed).toContainEqual(['/api/v1/admin/configuration', 'root-key'])
    expect(
      observed.filter(
        ([url]) => url === '/api/v1/admin/accounts/default/users',
      ),
    ).toHaveLength(1)
  } finally {
    cleanup()
    client.clear()
    localStorage.removeItem('ov_console_connection')
    await new Promise<void>((resolve) => server.close(() => resolve()))
  }
})
