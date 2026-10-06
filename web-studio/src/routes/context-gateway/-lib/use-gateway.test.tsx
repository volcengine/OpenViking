// @vitest-environment jsdom
import type * as React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type * as Api from './api'
import {
  gatewayQueryKey,
  gatewayScope,
  useGateway,
  useLogs,
  useTools,
  useUpstreams,
} from './use-gateway'

const state = vi.hoisted(() => ({ role: 'admin' }))
const api = vi.hoisted(() => ({
  listUpstreams: vi.fn(),
  listLogs: vi.fn(),
  listTools: vi.fn(),
}))

vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'https://ov.example.com',
      accountId: 'acme',
      userId: 'admin',
      apiKey: 'data-key',
      adminApiKey: 'admin-key',
    },
    connectionRole: state.role,
    isConnectionRoleLoading: false,
    serverMode: 'api_key',
  }),
}))
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof Api>()),
  listUpstreams: api.listUpstreams,
  listLogs: api.listLogs,
  listTools: api.listTools,
}))

let client: QueryClient
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
)

beforeEach(() => {
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  state.role = 'admin'
  api.listUpstreams.mockReset()
  api.listLogs.mockReset()
  api.listTools.mockReset()
})
afterEach(cleanup)

it('scopes data by server, account and a hash of the admin key', () => {
  const scope = gatewayScope('https://ov.example.com', 'acme', 'admin-key')
  expect(scope.slice(0, 2)).toEqual(['https://ov.example.com', 'acme'])
  expect(scope[2]).not.toContain('admin-key')
  expect(gatewayQueryKey(scope, 'logs', 50)).toEqual([
    'context-gateway',
    scope,
    'logs',
    50,
  ])
})

it('manages with the admin key for account administrators', () => {
  const { result } = renderHook(() => useGateway(), { wrapper })
  expect(result.current.allowed).toBe(true)
  expect(result.current.connection).toEqual({
    baseUrl: 'https://ov.example.com',
    accountId: 'acme',
    userId: 'admin',
    apiKey: 'admin-key',
  })
})

it('does not load anything for regular users', () => {
  state.role = 'user'
  const { result } = renderHook(
    () => ({ upstreams: useUpstreams(), tools: useTools() }),
    { wrapper },
  )
  expect(result.current.upstreams.fetchStatus).toBe('idle')
  expect(result.current.tools.fetchStatus).toBe('idle')
  expect(api.listUpstreams).not.toHaveBeenCalled()
  expect(api.listTools).not.toHaveBeenCalled()
})

it('scopes and refreshes the tools catalog through the admin connection', async () => {
  const tools = [{ name: 'future_tool', description: 'New tool' }]
  api.listTools.mockResolvedValue(tools)
  const { result } = renderHook(
    () => ({ gateway: useGateway(), tools: useTools() }),
    { wrapper },
  )
  await waitFor(() => expect(result.current.tools.data).toEqual(tools))
  expect(api.listTools).toHaveBeenCalledWith(result.current.gateway.connection)
  expect(
    client.getQueryData(gatewayQueryKey(result.current.gateway.scope, 'tools')),
  ).toEqual(tools)
  expect(
    client.getQueryData(
      gatewayQueryKey(
        gatewayScope('https://ov.example.com', 'other', 'admin-key'),
        'tools',
      ),
    ),
  ).toBeUndefined()
  await result.current.gateway.invalidate('tools')
  expect(api.listTools).toHaveBeenCalledTimes(2)
})

it('loads resources with the admin connection and invalidates by resource', async () => {
  api.listUpstreams.mockResolvedValue([{ id: 'u' }])
  api.listLogs.mockResolvedValue([])
  const { result } = renderHook(
    () => ({
      gateway: useGateway(),
      upstreams: useUpstreams(),
      logs: useLogs(25),
    }),
    { wrapper },
  )
  await waitFor(() =>
    expect(result.current.upstreams.data).toEqual([{ id: 'u' }]),
  )
  expect(api.listUpstreams.mock.calls[0][0].apiKey).toBe('admin-key')
  expect(api.listLogs.mock.calls[0][1]).toBe(25)

  await result.current.gateway.invalidate('upstreams')
  expect(api.listUpstreams).toHaveBeenCalledTimes(2)
  expect(api.listLogs).toHaveBeenCalledTimes(1)

  await result.current.gateway.invalidate()
  expect(api.listLogs).toHaveBeenCalledTimes(2)
})
