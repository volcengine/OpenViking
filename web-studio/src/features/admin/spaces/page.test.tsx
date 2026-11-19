// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { DataSpacesPage } from './page'

const state = vi.hoisted(() => ({
  role: 'root',
  provider: 'opensource',
  accounts: vi.fn(),
  switchAccount: vi.fn(),
  navigate: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://server.test',
      apiKey: 'data-key',
      adminApiKey: 'root-key',
      accountId: 'one',
      userId: 'alice',
    },
    connectionRole: state.role,
    isConnectionRoleLoading: false,
    serverMode: 'api_key',
    identityScopeKey: 'scope',
    switchManagementAccount: state.switchAccount,
  }),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: state.provider }),
}))
vi.mock('#/shared/api/admin', () => ({ fetchAdminAccounts: state.accounts }))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => state.navigate }))
vi.mock('#/features/connection/components/scope-create-account', () => ({
  ScopeCreateAccount: () => <button>Create space</button>,
}))
vi.mock('#/features/admin/users/components/delete-account-button', () => ({
  DeleteAccountButton: () => <button>Delete space</button>,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, options?: { count?: number }) =>
      options?.count === undefined ? key : `${key}:${options.count}`,
  }),
}))
function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <DataSpacesPage />
    </QueryClientProvider>,
  )
}
beforeEach(() => {
  state.role = 'root'
  state.provider = 'opensource'
  state.accounts.mockResolvedValue([
    { accountId: 'one', userCount: 4 },
    { accountId: 'two', userCount: 1 },
  ])
  state.switchAccount.mockResolvedValue(undefined)
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
it('searches spaces and enters the selected space before opening its members', async () => {
  mount()
  await screen.findByText('two')
  expect(state.accounts).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: 'root-key' }),
    expect.any(AbortSignal),
  )
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'two' } })
  expect(screen.queryByText('one')).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'spaces.memberCount:1' }))
  await waitFor(() =>
    expect(state.navigate).toHaveBeenCalledWith({ to: '/admin/users' }),
  )
  expect(state.switchAccount).toHaveBeenCalledWith('two', {
    preserveProfile: true,
  })
  expect(state.switchAccount.mock.invocationCallOrder[0]).toBeLessThan(
    state.navigate.mock.invocationCallOrder[0],
  )
})
it.each([
  ['admin', 'opensource'],
  ['root', 'volcengine'],
])('does not list accounts for %s on %s', (role, provider) => {
  state.role = role
  state.provider = provider
  mount()
  expect(screen.getByText('spaces.unavailable')).toBeTruthy()
  expect(state.accounts).not.toHaveBeenCalled()
  expect(screen.queryByRole('button', { name: 'Create space' })).toBeNull()
})
