// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import type { ConnectionProfile } from '#/lib/connection/profiles'
import { AccountSwitcher } from './account-switcher'

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
vi.mock('#/lib/admin', () => ({
  fetchAdminAccounts: fetchAccounts,
  fetchAdminUsers: fetchUsers,
  createAdminAccount: vi.fn(),
}))
vi.mock('#/hooks/use-app-connection', () => ({
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
vi.mock('#/hooks/use-studio-service', () => ({
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
  fireEvent.click(screen.getByText('default'))
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
    screen.getByRole('button', { name: /Open-source default/ }),
  ).toBeTruthy()
  const hosted = screen.getByRole('button', { name: /Hosted space/ })
  expect(
    hosted.querySelector('[role="img"][aria-label="provider.hosted"]'),
  ).toBeTruthy()
  fireEvent.click(hosted)
  expect(activateProfile).toHaveBeenCalledWith('hosted')
  expect(switchAccount).not.toHaveBeenCalled()

  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  await waitFor(() => expect(screen.getByText('team')).toBeTruthy())
  fireEvent.click(screen.getByText('team'))
  await waitFor(() => expect(switchAccount).toHaveBeenCalledWith('team'))
  expect(fetchUsers).not.toHaveBeenCalled()
  expect(switchIdentity).not.toHaveBeenCalled()
  client.clear()
})
