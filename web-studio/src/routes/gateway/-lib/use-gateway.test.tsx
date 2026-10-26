// @vitest-environment jsdom
import type * as React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

import type * as Api from './api'
import { setManagementKey, lockManagementKey } from './management-key'
import {
  gatewayQueryKey,
  gatewayScope,
  useGateway,
  useLogs,
  useTools,
  useUpstreams,
} from './use-gateway'

const state = vi.hoisted(() => ({
  role: 'admin',
  serverMode: 'api_key',
  apiKey: 'data-key',
  adminApiKey: 'admin-key',
}))
const api = vi.hoisted(() => ({
  getAccess: vi.fn(),
  listUpstreams: vi.fn(),
  listLogs: vi.fn(),
  listTools: vi.fn(),
}))

vi.mock('#/hooks/use-studio-service', () => ({
  useStudioService: () => ({ ready: true, provider: 'opensource' }),
}))

vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'https://ov.example.com',
      accountId: 'acme',
      userId: 'admin',
      apiKey: state.apiKey,
      adminApiKey: state.adminApiKey,
    },
    connectionRole: state.role,
    isConnectionRoleLoading: false,
    serverMode: state.serverMode,
  }),
}))
vi.mock('./api', async (importOriginal) => ({
  ...(await importOriginal<typeof Api>()),
  getAccess: api.getAccess,
  listUpstreams: api.listUpstreams,
  listLogs: api.listLogs,
  listTools: api.listTools,
}))

let client: QueryClient
const wrapper = ({ children }: { children: React.ReactNode }) => (
  <QueryClientProvider client={client}>{children}</QueryClientProvider>
)

beforeEach(() => {
  sessionStorage.clear()
  state.serverMode = 'api_key'
  state.apiKey = 'data-key'
  state.adminApiKey = 'admin-key'
  api.getAccess.mockResolvedValue({
    managed: false,
    can_write_objects: true,
    can_import_vlm: true,
    can_issue_for_others: true,
    role: 'admin',
    account_id: 'acme',
    user_id: 'admin',
    warnings: [],
  })
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
    'gateway',
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
    managementKey: '',
  })
})

it('loads account objects for regular users', async () => {
  api.listUpstreams.mockResolvedValue([])
  api.listTools.mockResolvedValue([])
  state.role = 'user'
  const { result } = renderHook(
    () => ({ upstreams: useUpstreams(), tools: useTools() }),
    { wrapper },
  )
  await waitFor(() => expect(result.current.upstreams.isSuccess).toBe(true))
  expect(api.listUpstreams).toHaveBeenCalled()
  expect(api.listTools).toHaveBeenCalled()
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

it('uses the ordinary OV key without an administrator key', () => {
  state.adminApiKey = ''
  state.role = 'user'
  const { result } = renderHook(() => useGateway(), { wrapper })
  expect(result.current.allowed).toBe(true)
  expect(result.current.connection.apiKey).toBe('data-key')
})
it.each(['dev', 'trusted'])(
  'asks /access in %s mode without either credential',
  (serverMode) => {
    state.serverMode = serverMode
    state.adminApiKey = ''
    state.apiKey = ''
    const { result } = renderHook(() => useGateway(), { wrapper })
    expect(result.current.allowed).toBe(true)
  },
)
it('partitions caches by the management key without storing the secret in query keys', () => {
  const locked = gatewayScope('http://ov', 'default', 'ov-key')
  const unlocked = gatewayScope('http://ov', 'default', 'ov-key', 'ovgm-secret')
  expect(locked).not.toEqual(unlocked)
  expect(JSON.stringify(unlocked)).not.toContain('ovgm-secret')
})

it('reloads gateway data in its own scope when the key is unlocked or locked', async () => {
  const baseUrl = 'https://ov.example.com'
  api.getAccess.mockImplementation(
    async (connection: Api.GatewayConnection) => ({
      managed: true,
      can_write_objects: Boolean(connection.managementKey),
      can_issue_for_others: false,
    }),
  )
  api.listUpstreams.mockImplementation(
    async (connection: Api.GatewayConnection) => [
      {
        id: 'u',
        ...(connection.managementKey ? { header_names: ['X-Secret'] } : {}),
      },
    ],
  )
  const { result } = renderHook(
    () => ({ gateway: useGateway(), upstreams: useUpstreams() }),
    { wrapper },
  )
  await waitFor(() => expect(result.current.gateway.access).toBeTruthy())
  expect(result.current.gateway.readOnly).toBe(true)
  expect(result.current.gateway.canIssueForOthers).toBe(false)
  const lockedScope = result.current.gateway.scope
  act(() => setManagementKey(baseUrl, 'ovgm-test-key'))
  await waitFor(() => expect(result.current.gateway.readOnly).toBe(false))
  await waitFor(() =>
    expect(result.current.upstreams.data?.[0].header_names).toEqual([
      'X-Secret',
    ]),
  )
  expect(
    client.getQueryData<Api.GatewayAccess>(
      gatewayQueryKey(lockedScope, 'access'),
    )?.can_write_objects,
  ).toBe(false)
  act(() => lockManagementKey(baseUrl))
  await waitFor(() => expect(result.current.gateway.readOnly).toBe(true))
  expect(result.current.upstreams.data?.[0].header_names).toBeUndefined()
})
