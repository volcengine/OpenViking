// @vitest-environment jsdom
import {
  AppConnectionProvider,
  useAppConnection,
} from '#/features/connection/hooks/use-app-connection'
import { useStudioMode } from '#/features/connection/hooks/use-studio-mode'
import { ovClient } from '#/shared/api/ov-client/index'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, render, waitFor } from '@testing-library/react'
import { once } from 'node:events'
import { createServer } from 'node:http'
import { afterEach, expect, it, vi } from 'vitest'

const routing = vi.hoisted(() => ({ pathname: '/home' }))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => vi.fn(),
  useRouterState: () => routing.pathname,
}))
vi.mock('#/features/connection/lib/credential-storage', () => ({
  readCredentialStorage: (key: string) => localStorage.getItem(key),
  writeCredentialStorage: (key: string, value: string) =>
    localStorage.setItem(key, value),
}))
afterEach(() => {
  cleanup()
  localStorage.clear()
  sessionStorage.clear()
})

it.each([
  ['/home', ''],
  ['/admin/users', ''],
  ['/home', 'old-user-key'],
])(
  'authenticates trusted scopes and switches keyless users from %s with previous User key %s',
  async (entry, dataKey) => {
    routing.pathname = entry
    const observed: Array<{
      path: string
      key?: string
      account?: string
      user?: string
    }> = []
    const server = createServer((req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*')
      res.setHeader('Access-Control-Allow-Headers', '*')
      if (req.method === 'OPTIONS') return void res.end()
      const path = req.url || ''
      const key = req.headers['x-api-key'] as string | undefined
      const account = req.headers['x-openviking-account'] as string | undefined
      const user = req.headers['x-openviking-user'] as string | undefined
      observed.push({ path, key, account, user })
      res.setHeader('Content-Type', 'application/json')
      if (path === '/health' && !key)
        return void res.end(
          JSON.stringify({ status: 'ok', auth_mode: 'trusted' }),
        )
      if (key !== 'trusted-root') {
        res.statusCode = 401
        return void res.end('{}')
      }
      if (path === '/health')
        return void res.end(
          JSON.stringify({
            status: 'ok',
            auth_mode: 'trusted',
            role: account && user ? 'user' : 'root',
            account_id: account || 'trusted',
            user_id: user || 'trusted',
          }),
        )
      if (!path.startsWith('/api/v1/admin') && (!account || !user)) {
        res.statusCode = 400
        return void res.end('{}')
      }
      res.end(JSON.stringify({ status: 'ok', result: [] }))
    })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const { port } = server.address() as { port: number }
    localStorage.setItem(
      'ov_console_connection',
      JSON.stringify({
        baseUrl: `http://127.0.0.1:${port}`,
        serviceSelection: 'opensource',
        accountId: 'team',
        userId: 'alice',
        apiKey: dataKey,
        adminApiKey: 'trusted-root',
      }),
    )
    let api!: ReturnType<typeof useAppConnection>
    let canManage = false
    function Capture() {
      api = useAppConnection()
      canManage = useStudioMode().canManage
      return null
    }
    const client = new QueryClient()
    const tree = () => (
      <QueryClientProvider client={client}>
        <AppConnectionProvider>
          <Capture />
        </AppConnectionProvider>
      </QueryClientProvider>
    )
    const view = render(tree())
    const settled = async (role: 'root' | 'user') => {
      await waitFor(() => expect(api.serverMode).toBe('trusted'))
      await waitFor(() => expect(api.connectionRole).toBe(role))
      await waitFor(() => expect(api.isConnectionRoleLoading).toBe(false))
    }
    try {
      await settled(entry.startsWith('/admin') ? 'root' : 'user')
      expect(canManage).toBe(true)
      routing.pathname = '/home'
      view.rerender(tree())
      await settled('user')
      await ovClient.client.get({ url: '/api/v1/fs/ls' })
      expect(observed.at(-1)).toEqual({
        path: '/api/v1/fs/ls',
        key: 'trusted-root',
        account: 'team',
        user: 'alice',
      })
      await act(() =>
        api.switchIdentity({
          accountId: 'other',
          userId: 'bob',
          apiKey: '',
          preserveProfile: true,
        }),
      )
      await settled('user')
      await ovClient.client.get({ url: '/api/v1/fs/ls' })
      expect(observed.at(-1)).toEqual({
        path: '/api/v1/fs/ls',
        key: 'trusted-root',
        account: 'other',
        user: 'bob',
      })
      expect(api.profiles).toHaveLength(1)
      expect(api.connection.apiKey).toBe('')
      await expect(
        ovClient.client.get({ url: '/api/v1/admin/accounts' }),
      ).rejects.toMatchObject({ statusCode: 403 })
      routing.pathname = '/admin/users'
      view.rerender(tree())
      await settled('root')
      expect(api.connection).toMatchObject({
        accountId: entry.startsWith('/admin') ? 'team' : 'other',
        adminApiKey: 'trusted-root',
      })
      await ovClient.client.get({ url: '/api/v1/admin/accounts' })
      expect(observed.at(-1)).toEqual({
        path: '/api/v1/admin/accounts',
        key: 'trusted-root',
        account: undefined,
        user: undefined,
      })
      await act(() =>
        api.switchManagementAccount('team', { preserveProfile: true }),
      )
      await waitFor(() => expect(api.connection.accountId).toBe('team'))
      expect(api.connection.userId).toBe('default')
      routing.pathname = '/home'
      view.rerender(tree())
      await settled('user')
      expect(canManage).toBe(true)
      expect(api.connection).toMatchObject({
        accountId: 'other',
        userId: 'bob',
      })
    } finally {
      view.unmount()
      client.clear()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  },
)
