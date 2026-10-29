// @vitest-environment jsdom
import { createServer } from 'node:http'
import { once } from 'node:events'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'

import { useStudioService } from './use-studio-service'
import { AppConnectionProvider, useAppConnection } from './use-app-connection'

const { navigate } = vi.hoisted(() => ({ navigate: vi.fn() }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => navigate,
  useRouterState: () => '/home',
}))

afterEach(() => {
  cleanup()
  navigate.mockReset()
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

it.each([401, 503])(
  'opens connection settings only for rejected credentials on startup (HTTP %s)',
  async (status) => {
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
        expect(navigate).toHaveBeenCalledWith({ to: '/settings' })
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
        role: 'unknown',
        account: 'account-a',
        user: 'alice',
      }),
    )
    expect(keys[0]).toBe('user-key')
    expect(keys).toContain('expired-control-key')
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
