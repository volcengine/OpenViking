// @vitest-environment jsdom
import { AccountSwitcher } from '#/features/connection/components/account-switcher'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const { state, fetchAccounts, fetchUsers, switchIdentity } = vi.hoisted(() => ({
  state: {
    identityScopeKey: 'source',
    connection: {
      accountId: 'default',
      userId: 'root',
      apiKey: 'root-key',
      adminApiKey: 'admin-key',
      baseUrl: 'http://source.test',
      serviceSelection: 'opensource' as const,
    },
  },
  fetchAccounts: vi.fn(),
  fetchUsers: vi.fn(),
  switchIdentity: vi.fn(),
}))

vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    ...state,
    profiles: [],
    activeProfileId: 'source',
    activateProfile: vi.fn(),
    connectionRole: 'root',
    isGatewayHost: false,
    isConnectionRoleLoading: false,
    serverMode: 'api_key',
    switchIdentity,
    setGeneratedCredential: vi.fn(),
  }),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: 'opensource' }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({
    children,
    to,
    ...props
  }: {
    children: ReactNode
    to: string
    className?: string
    onClick?: () => void
  }) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))
vi.mock('#/shared/api/admin', () => ({
  fetchAdminAccounts: fetchAccounts,
  fetchAdminUsers: fetchUsers,
  createAdminAccount: vi.fn(),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

let queryClient: QueryClient
beforeEach(() => {
  state.identityScopeKey = 'source'
  vi.clearAllMocks()
  fetchAccounts.mockResolvedValue([
    { accountId: 'default', userCount: 1 },
    { accountId: 'team', userCount: 1 },
  ])
  queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
})
afterEach(() => {
  cleanup()
  queryClient.clear()
})

function renderSwitcher() {
  return render(
    <QueryClientProvider client={queryClient}>
      <AccountSwitcher />
    </QueryClientProvider>,
  )
}

async function selectTeam() {
  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  fireEvent.click(screen.getByRole('button', { name: 'serverAccountsTitle' }))
  await waitFor(() => expect(screen.getByText('team')).toBeTruthy())
  fireEvent.click(screen.getByText('team'))
  await waitFor(() => expect(fetchUsers).toHaveBeenCalledTimes(1))
}

const usersWithoutKeys = [
  {
    accountId: 'team',
    userId: 'alice',
    role: 'user',
    keyPrefix: 'ovk_123',
  },
]

it('does not open the manual key dialog when the profile changed during user lookup', async () => {
  let finishUsers!: (users: typeof usersWithoutKeys) => void
  fetchUsers.mockReturnValue(
    new Promise((resolve) => {
      finishUsers = resolve
    }),
  )
  const view = renderSwitcher()
  await selectTeam()

  state.identityScopeKey = 'target'
  view.rerender(
    <QueryClientProvider client={queryClient}>
      <AccountSwitcher />
    </QueryClientProvider>,
  )
  await act(async () => finishUsers(usersWithoutKeys))

  expect(screen.queryByText('manualSwitch.title')).toBeNull()
  expect(switchIdentity).not.toHaveBeenCalled()
})

it('closes an already open manual key dialog when the identity scope changes', async () => {
  fetchUsers.mockResolvedValue(usersWithoutKeys)
  const view = renderSwitcher()
  await selectTeam()
  await waitFor(() =>
    expect(screen.getByText('manualSwitch.title')).toBeTruthy(),
  )

  state.identityScopeKey = 'target'
  view.rerender(
    <QueryClientProvider client={queryClient}>
      <AccountSwitcher />
    </QueryClientProvider>,
  )

  await waitFor(() =>
    expect(screen.queryByText('manualSwitch.title')).toBeNull(),
  )
})
