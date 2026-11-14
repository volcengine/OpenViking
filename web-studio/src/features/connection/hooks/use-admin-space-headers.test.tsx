// @vitest-environment jsdom
import type * as ConnectionHealth from '#/features/connection/lib/health'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import { useAdminSpaceHeaders } from './use-admin-space-headers'
import { OvClientError } from '#/shared/api/ov-client/index'

const mocks = vi.hoisted(() => ({ users: vi.fn(), health: vi.fn() }))
const state = vi.hoisted(() => ({
  connection: {
    baseUrl: 'http://localhost:1933',
    accountId: 'default',
    userId: 'root',
    adminApiKey: 'root-key',
    apiKey: 'data-key',
  },
  connectionRole: 'root',
  isConnectionRoleLoading: false,
  serverMode: 'api_key',
}))
vi.mock('./use-app-connection', () => ({ useAppConnection: () => state }))
vi.mock('#/shared/api/admin', () => ({ fetchAdminUsers: mocks.users }))
vi.mock('#/features/connection/lib/health', async (importOriginal) => ({
  ...(await importOriginal<typeof ConnectionHealth>()),
  fetchServerHealth: mocks.health,
}))
const clients: QueryClient[] = []
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
})
beforeEach(() => {
  state.connection.accountId = 'default'
  state.connection.adminApiKey = 'root-key'
  state.connectionRole = 'root'
  state.serverMode = 'api_key'
  mocks.users
    .mockReset()
    .mockImplementation(async (_connection, accountId) => [
      { accountId, userId: 'admin', role: 'admin', apiKey: `${accountId}-key` },
    ])
  mocks.health.mockReset().mockImplementation(async (_url, headers) => ({
    role: 'admin',
    account_id: headers['X-API-Key'].replace(/-key$/, ''),
    user_id: 'admin',
  }))
})
function setup(management = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  function Wrapper({ children }: PropsWithChildren) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>
  }
  return {
    client,
    ...renderHook(() => useAdminSpaceHeaders(management), { wrapper: Wrapper }),
  }
}
it('obtains and validates a space Admin key while preserving Root', async () => {
  const { result } = setup()
  await waitFor(() =>
    expect(result.current.headers).toEqual({ 'X-API-Key': 'default-key' }),
  )
  expect(mocks.users).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: 'root-key' }),
    'default',
    true,
  )
  expect(state.connection.adminApiKey).toBe('root-key')
})
it('drops the previous space credential immediately and isolates late responses', async () => {
  let resolveDefault!: (value: unknown[]) => void
  mocks.users.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        resolveDefault = resolve
      }),
  )
  const { result, rerender } = setup()
  state.connection.accountId = 'wulv'
  rerender()
  expect(result.current.headers).toBeNull()
  await waitFor(() =>
    expect(result.current.headers).toEqual({ 'X-API-Key': 'wulv-key' }),
  )
  await act(async () =>
    resolveDefault([{ userId: 'admin', role: 'admin', apiKey: 'default-key' }]),
  )
  expect(result.current.headers).toEqual({ 'X-API-Key': 'wulv-key' })
})
it('skips unavailable keys and credentials whose verified account does not match', async () => {
  mocks.users.mockResolvedValue([
    { userId: 'user', role: 'user', apiKey: 'default-key' },
    { userId: 'admin', role: 'admin', keyPrefix: 'hash-only' },
    { userId: 'admin', role: 'admin', apiKey: 'other-key' },
    { userId: 'admin', role: 'admin', apiKey: 'default-key' },
  ])
  const { result } = setup()
  await waitFor(() =>
    expect(result.current.headers).toEqual({ 'X-API-Key': 'default-key' }),
  )
  expect(mocks.health).toHaveBeenCalledTimes(2)
})
it('blocks tenant requests if no usable Admin key exists', async () => {
  mocks.users.mockResolvedValue([
    { userId: 'admin', role: 'admin', keyPrefix: 'hash-only' },
  ])
  const { result } = setup()
  await waitFor(() => expect(result.current.isLoading).toBe(false))
  expect(result.current.headers).toBeNull()
  expect(mocks.health).not.toHaveBeenCalled()
})
it('uses an existing Account Admin key directly', () => {
  state.connectionRole = 'admin'
  const { result } = setup()
  expect(result.current.headers).toEqual({ 'X-API-Key': 'root-key' })
  expect(mocks.users).not.toHaveBeenCalled()
})
it('leaves workbench credentials to the existing client', () => {
  const { result } = setup(false)
  expect(result.current.headers).toBeUndefined()
  expect(mocks.users).not.toHaveBeenCalled()
})
it('refreshes a revoked space credential using Root', async () => {
  const { client, result } = setup()
  await waitFor(() =>
    expect(result.current.headers).toEqual({ 'X-API-Key': 'default-key' }),
  )
  const oldScope = result.current.scopeKey
  mocks.users.mockResolvedValue([
    { userId: 'admin', role: 'admin', apiKey: 'replacement' },
  ])
  mocks.health.mockResolvedValue({
    role: 'admin',
    account_id: 'default',
    user_id: 'admin',
  })
  await act(async () => {
    await client
      .fetchQuery({
        queryKey: ['console-audit-logs', oldScope],
        meta: { adminSpaceScope: oldScope },
        queryFn: () =>
          Promise.reject(
            new OvClientError({
              code: 'UNAUTHENTICATED',
              message: 'Revoked',
              statusCode: 401,
            }),
          ),
      })
      .catch(() => {})
  })
  await waitFor(() =>
    expect(result.current.headers).toEqual({ 'X-API-Key': 'replacement' }),
  )
  expect(mocks.users).toHaveBeenCalledTimes(2)
})
