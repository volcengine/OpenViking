// @vitest-environment jsdom
import { useIdentityDirectory } from '#/features/oauth/hooks/use-identity-directory'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, renderHook, waitFor } from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  mode: 'workbench',
  path: '/oauth/verify',
  accounts: vi.fn(),
  users: vi.fn(),
}))
vi.mock('@tanstack/react-router', () => ({ useRouterState: () => state.path }))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://server.test',
      apiKey: 'user-key',
      adminApiKey: 'root-key',
      accountId: 'team',
      userId: 'alice',
    },
    connectionRole: 'root',
    serverMode: 'api_key',
    isConnectionRoleLoading: false,
    studioMode: state.mode,
  }),
}))
vi.mock('#/shared/api/admin', () => ({
  fetchAdminAccounts: state.accounts,
  fetchAdminUsers: state.users,
}))
const clients: QueryClient[] = []
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  return renderHook(useIdentityDirectory, {
    wrapper: ({ children }: PropsWithChildren) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    ),
  })
}
beforeEach(() => {
  vi.clearAllMocks()
  state.mode = 'workbench'
  state.path = '/oauth/verify'
  state.accounts.mockResolvedValue([{ accountId: 'team' }])
  state.users.mockResolvedValue([
    { accountId: 'team', userId: 'alice', apiKey: 'alice-key' },
  ])
})
afterEach(() => {
  cleanup()
  clients.forEach((client) => client.clear())
  clients.length = 0
})
it.each(['workbench', 'management'])(
  'does not read Root directories from a public OAuth page with remembered mode %s',
  (mode) => {
    state.mode = mode
    const { result } = mount()
    expect(result.current.available).toBe(false)
    expect(state.accounts).not.toHaveBeenCalled()
    expect(state.users).not.toHaveBeenCalled()
  },
)
it('retains delegated identity selection on the explicit admin authorization page', async () => {
  state.mode = 'management'
  state.path = '/admin/oauth/verify'
  const { result } = mount()
  await waitFor(() => expect(result.current.users).toHaveLength(1))
  expect(result.current.available).toBe(true)
  expect(state.accounts).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: 'root-key' }),
  )
  expect(result.current.resolveUserKey('team', 'alice')).toBe('alice-key')
})
