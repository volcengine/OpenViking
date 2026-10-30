// @vitest-environment jsdom
import {
  AppConnectionProvider,
  useAppConnection,
} from '#/features/connection/hooks/use-app-connection'
import { useStudioMode } from '#/features/connection/hooks/use-studio-mode'
import { resolveStudioManagementCapabilities } from '#/features/connection/lib/studio-permissions'
import { createAdminUser, updateAdminUserRole } from '#/shared/api/admin'
import { ovClient } from '#/shared/api/ov-client/index'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, waitFor } from '@testing-library/react'
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
  routing.pathname = '/home'
})

it.each([
  ['user-key', 'root-key', 'root'],
  ['', 'root-key', 'root'],
  ['user-key', 'account-admin-key', 'admin'],
  ['', 'account-admin-key', 'admin'],
] as const)(
  'isolates the data key %s from management key %s and authorizes management role %s',
  async (userKey, controlKey, controlRole) => {
    const observed: Array<{ url?: string; key?: string }> = []
    const server = createServer((req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*')
      res.setHeader('Access-Control-Allow-Headers', '*')
      if (req.method === 'OPTIONS') {
        res.end()
        return
      }
      const key = req.headers['x-api-key'] as string | undefined
      observed.push({ url: req.url, key })
      res.setHeader('Content-Type', 'application/json')
      res.end(
        JSON.stringify(
          req.url === '/health'
            ? {
                status: 'ok',
                version: '0.5.1',
                auth_mode: 'api_key',
                ...(key
                  ? {
                      account_id: 'team',
                      user_id: key === controlKey ? 'manager' : 'alice',
                      role: key === controlKey ? controlRole : 'user',
                    }
                  : {}),
              }
            : { status: 'ok', result: [] },
        ),
      )
    })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const address = server.address() as { port: number }
    localStorage.setItem(
      'ov_console_connection',
      JSON.stringify({
        baseUrl: `http://127.0.0.1:${address.port}`,
        serviceSelection: 'opensource',
        accountId: 'team',
        userId: 'alice',
        apiKey: userKey,
        adminApiKey: controlKey,
      }),
    )
    let api!: ReturnType<typeof useAppConnection>
    let canEnterManagement = false
    function Capture() {
      api = useAppConnection()
      canEnterManagement = useStudioMode().canManage
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
    try {
      await waitFor(() => expect(api.serverMode).toBe('api_key'))
      await waitFor(() => expect(api.isConnectionRoleLoading).toBe(false))
      expect(api.connectionRole).toBe(userKey ? 'user' : 'unknown')
      expect(canEnterManagement).toBe(true)
      expect(observed.some((r) => r.key === controlKey)).toBe(false)
      expect(ovClient.getConnection().adminApiKey).toBe('')
      await ovClient.client.get({ url: '/api/v1/console/dashboard/summary' })
      expect(observed.at(-1)?.key || '').toBe(userKey)
      await expect(
        ovClient.client.get({ url: '/api/v1/admin/accounts' }),
      ).rejects.toMatchObject({ statusCode: 403 })
      expect(observed.some((r) => r.url?.startsWith('/api/v1/admin'))).toBe(
        false,
      )

      routing.pathname = '/admin/users'
      view.rerender(tree())
      await waitFor(() => expect(api.connectionRole).toBe(controlRole))
      expect(ovClient.getConnection().adminApiKey).toBe(controlKey)
      const managementPath =
        controlRole === 'root'
          ? '/api/v1/admin/accounts'
          : '/api/v1/admin/accounts/team/users'
      await ovClient.client.get({ url: managementPath })
      expect(observed).toContainEqual({ url: managementPath, key: controlKey })
      if (controlRole === 'admin')
        expect(observed.some((r) => r.url === '/api/v1/admin/accounts')).toBe(
          false,
        )

      observed.length = 0
      routing.pathname = '/home'
      view.rerender(tree())
      await waitFor(() =>
        expect(api.connectionRole).toBe(userKey ? 'user' : 'unknown'),
      )
      await waitFor(() => expect(api.serverHealth).not.toBeNull())
      await ovClient.client.get({ url: '/api/v1/console/dashboard/summary' })
      expect(observed.some((r) => r.key === controlKey)).toBe(false)
      expect(observed.at(-1)?.key || '').toBe(userKey)
      expect(api.connection.adminApiKey).toBe(controlKey)
    } finally {
      view.unmount()
      client.clear()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  },
)

it.each(['/home', '/admin/users'])(
  'accepts an Admin User Key without Root credentials from %s',
  async (entry) => {
    routing.pathname = entry
    const observed: Array<{ url?: string; method?: string; key?: string }> = []
    const server = createServer((req, res) => {
      res.setHeader('Access-Control-Allow-Origin', '*')
      res.setHeader('Access-Control-Allow-Headers', '*')
      res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, OPTIONS')
      if (req.method === 'OPTIONS') return void res.end()
      const key = req.headers['x-api-key'] as string | undefined
      observed.push({ url: req.url, method: req.method, key })
      res.setHeader('Content-Type', 'application/json')
      if (key !== 'account-admin-key') {
        res.statusCode = 401
        return void res.end('{}')
      }
      if (req.url === '/api/v1/admin/accounts') {
        res.statusCode = 403
        return void res.end('{}')
      }
      res.end(
        JSON.stringify(
          req.url === '/health'
            ? {
                status: 'ok',
                version: '0.5.1',
                auth_mode: 'api_key',
                account_id: 'team',
                user_id: 'manager',
                role: 'admin',
              }
            : { status: 'ok', result: {} },
        ),
      )
    })
    server.listen(0, '127.0.0.1')
    await once(server, 'listening')
    const address = server.address() as { port: number }
    localStorage.setItem(
      'ov_console_connection',
      JSON.stringify({
        baseUrl: `http://127.0.0.1:${address.port}`,
        serviceSelection: 'opensource',
        accountId: 'stale-account',
        userId: 'stale-user',
        apiKey: 'account-admin-key',
        adminApiKey: '',
      }),
    )
    let api!: ReturnType<typeof useAppConnection>
    let canEnterManagement = false
    function Capture() {
      api = useAppConnection()
      canEnterManagement = useStudioMode().canManage
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
    try {
      await waitFor(() => expect(canEnterManagement).toBe(true))
      await waitFor(() => expect(api.connectionRole).toBe('admin'))
      await waitFor(() =>
        expect(api.connection).toMatchObject({
          accountId: 'team',
          userId: 'manager',
          apiKey: 'account-admin-key',
          adminApiKey: 'account-admin-key',
        }),
      )
      routing.pathname = '/admin/users'
      view.rerender(tree())
      await waitFor(() => expect(api.isConnectionRoleLoading).toBe(false))
      expect(canEnterManagement).toBe(true)
      expect(
        resolveStudioManagementCapabilities({
          hasControlCredential: Boolean(api.connection.adminApiKey),
          isRoleLoading: api.isConnectionRoleLoading,
          role: api.connectionRole,
          serverMode: api.serverMode,
        }),
      ).toEqual({ canManageUsers: true, canManageAccounts: false })
      const management = {
        ...api.connection,
        apiKey: api.connection.adminApiKey,
      }
      await createAdminUser(management, {
        accountId: 'team',
        userId: 'new-user',
        role: 'user',
      })
      await updateAdminUserRole(management, {
        accountId: 'team',
        userId: 'new-user',
        role: 'admin',
      })
      expect(observed).toContainEqual({
        url: '/api/v1/admin/accounts/team/users',
        method: 'POST',
        key: 'account-admin-key',
      })
      expect(observed).toContainEqual({
        url: '/api/v1/admin/accounts/team/users/new-user/role',
        method: 'PUT',
        key: 'account-admin-key',
      })
      expect(
        observed.some((request) => request.url === '/api/v1/admin/accounts'),
      ).toBe(false)
    } finally {
      view.unmount()
      client.clear()
      await new Promise<void>((resolve) => server.close(() => resolve()))
    }
  },
)
