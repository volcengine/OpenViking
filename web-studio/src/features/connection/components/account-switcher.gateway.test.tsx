// @vitest-environment jsdom
import { AccountSwitcher } from '#/features/connection/components/account-switcher'
import type { ConnectionProfile } from '#/features/connection/lib/profiles'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'

const {
  fetchAccounts,
  fetchUsers,
  switchAccount,
  switchIdentity,
  activateProfile,
  savedProfileState,
} = vi.hoisted(() => ({
  fetchAccounts: vi.fn(),
  fetchUsers: vi.fn(),
  switchAccount: vi.fn(),
  switchIdentity: vi.fn(),
  activateProfile: vi.fn(),
  savedProfileState: { profiles: [] as Array<ConnectionProfile> },
}))
vi.mock('#/shared/api/admin', () => ({
  fetchAdminAccounts: fetchAccounts,
  fetchAdminUsers: fetchUsers,
  createAdminAccount: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    profiles: savedProfileState.profiles,
    activeProfileId: 'default',
    activateProfile,
    connection: {
      accountId: 'default',
      userId: 'root',
      apiKey: 'root-login-key',
      adminApiKey: '',
      baseUrl: 'http://gateway.test',
    },
    connectionRole: 'root',
    isGatewayHost: true,
    isConnectionRoleLoading: false,
    serverMode: 'api_key',
    switchManagementAccount: switchAccount,
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
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(() => {
  cleanup()
  savedProfileState.profiles = []
  vi.clearAllMocks()
})
it('root selects an account through the read-only list without requesting user credentials or offering creation', async () => {
  fetchAccounts.mockResolvedValue([
    { accountId: 'default', userCount: 1 },
    { accountId: 'team', userCount: 2 },
  ])
  switchAccount.mockResolvedValue(undefined)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <AccountSwitcher />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  fireEvent.click(screen.getByRole('button', { name: 'serverAccountsTitle' }))
  await waitFor(() => expect(screen.getByText('team')).toBeTruthy())
  expect(screen.queryByText('create')).toBeNull()
  expect(fetchAccounts).toHaveBeenCalledWith(
    expect.objectContaining({
      apiKey: 'root-login-key',
      baseUrl: 'http://gateway.test',
    }),
  )
  fireEvent.click(screen.getByText('team'))
  await waitFor(() => expect(switchAccount).toHaveBeenCalledWith('team'))
  expect(fetchUsers).not.toHaveBeenCalled()
  expect(switchIdentity).not.toHaveBeenCalled()
  client.clear()
})

it('keeps saved connections and server account switching available to a gateway root', async () => {
  savedProfileState.profiles = [
    {
      id: 'default',
      name: 'Open-source default',
      connection: {
        accountId: 'default',
        userId: 'root',
        apiKey: 'root-login-key',
        adminApiKey: '',
        baseUrl: 'http://gateway.test',
        serviceSelection: 'opensource',
      },
    },
    {
      id: 'hosted',
      name: 'Hosted space',
      connection: {
        accountId: 'study',
        userId: 'alice',
        apiKey: 'hosted-key',
        adminApiKey: '',
        baseUrl: 'https://hosted.example/openviking',
        serviceSelection: 'volcengine',
      },
    },
  ]
  fetchAccounts.mockResolvedValue([
    { accountId: 'default', userCount: 1 },
    { accountId: 'team', userCount: 2 },
  ])
  switchAccount.mockResolvedValue(undefined)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <AccountSwitcher />
    </QueryClientProvider>,
  )

  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  expect(
    screen
      .getByRole('button', { name: 'serverAccountsTitle' })
      .getAttribute('aria-pressed'),
  ).toBe('false')
  expect(
    screen
      .getByRole('button', { name: 'identities' })
      .getAttribute('aria-pressed'),
  ).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: 'identities' }))
  expect(screen.getByTitle('Open-source default')).toBeTruthy()
  const hostedService = screen.getByRole('button', { name: 'provider.hosted' })
  fireEvent.click(hostedService)
  expect(activateProfile).not.toHaveBeenCalled()
  expect(
    screen.queryByRole('button', { name: 'serverAccountsTitle' }),
  ).toBeNull()
  fireEvent.click(screen.getByTitle('Hosted space'))
  expect(activateProfile).toHaveBeenCalledWith('hosted')
  expect(switchAccount).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  fireEvent.click(screen.getByRole('button', { name: 'serverAccountsTitle' }))
  await waitFor(() => expect(screen.getByText('team')).toBeTruthy())
  fireEvent.click(screen.getByText('team'))
  await waitFor(() => expect(switchAccount).toHaveBeenCalledWith('team'))
  expect(fetchUsers).not.toHaveBeenCalled()
  expect(switchIdentity).not.toHaveBeenCalled()
  client.clear()
})
