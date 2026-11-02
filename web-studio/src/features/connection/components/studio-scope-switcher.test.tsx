// @vitest-environment jsdom
import { StudioScopeSwitcher } from '#/features/connection/components/studio-scope-switcher'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
} from '@testing-library/react'
import type { PropsWithChildren } from 'react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({
  role: 'root',
  serverMode: 'api_key',
  accountId: 'team-a',
  scope: 'first',
  apiKey: 'alice-key',
  hasHistory: true,
  profileApiKey: 'alice-key',
  activeProfileId: 'one',
  activateProfile: vi.fn(),
  switchIdentity: vi.fn(),
  switchManagementAccount: vi.fn(),
  accounts: vi.fn(),
  users: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://server.test',
      accountId: state.accountId,
      userId: 'alice',
      apiKey: state.apiKey,
      adminApiKey: state.role === 'user' ? '' : 'root-key',
    },
    profiles: [
      {
        id: 'one',
        name: 'Development',
        identities: state.hasHistory
          ? [
              {
                baseUrl: 'http://server.test',
                accountId: 'team-a',
                userId: 'bob',
                apiKey: 'bob-key',
              },
              {
                baseUrl: 'http://server.test',
                accountId: 'team-b',
                userId: 'carol',
                apiKey: 'carol-key',
              },
            ]
          : [],
        connection: {
          baseUrl: 'http://server.test',
          accountId: 'team-a',
          userId: 'alice',
          apiKey: state.profileApiKey,
        },
      },
      {
        id: 'two',
        name: 'Same server, another configuration',
        connection: {
          baseUrl: 'http://server.test',
          accountId: 'other',
          userId: 'jane',
          apiKey: 'jane-key',
        },
      },
      {
        id: 'three',
        name: 'Production',
        connection: {
          baseUrl: 'https://production.test',
          accountId: 'production-team',
          userId: 'production-user',
          apiKey: 'production-key',
        },
      },
    ],
    activeProfileId: state.activeProfileId,
    identityScopeKey: state.scope,
    connectionRole: state.role,
    isConnectionRoleLoading: false,
    serverMode: state.serverMode,
    isGatewayHost: false,
    activateProfile: state.activateProfile,
    switchIdentity: state.switchIdentity,
    switchManagementAccount: state.switchManagementAccount,
  }),
}))
vi.mock('#/features/connection/hooks/use-studio-service', () => ({
  useStudioService: () => ({ provider: 'opensource' }),
}))
vi.mock('#/shared/api/admin', () => ({
  fetchAdminAccounts: state.accounts,
  fetchAdminUsers: state.users,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, children, ...props }: PropsWithChildren<{ to: string }>) => (
    <a href={to} {...props}>
      {children}
    </a>
  ),
}))
const clients: QueryClient[] = []
beforeEach(() => {
  vi.clearAllMocks()
  Object.assign(state, {
    role: 'root',
    serverMode: 'api_key',
    accountId: 'team-a',
    scope: 'first',
    apiKey: 'alice-key',
    hasHistory: true,
    profileApiKey: 'alice-key',
    activeProfileId: 'one',
  })
  state.accounts.mockResolvedValue([
    { accountId: 'team-a' },
    { accountId: 'team-b' },
  ])
  state.users.mockImplementation(async (_connection, account) => [
    {
      accountId: account,
      userId: account === 'team-a' ? 'bob' : 'carol',
      role: 'user',
      apiKey: account === 'team-a' ? 'bob-key' : 'carol-key',
    },
  ])
  state.switchIdentity.mockResolvedValue(undefined)
  state.switchManagementAccount.mockResolvedValue(undefined)
})
afterEach(() => {
  cleanup()
  clients.forEach((client) => client.clear())
  clients.length = 0
})
function mount(
  settingsPath: '/settings' | '/admin/settings' = '/settings',
  showAccountScope = true,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  const tree = (managementMode = settingsPath === '/admin/settings') => (
    <QueryClientProvider client={client}>
      <StudioScopeSwitcher
        key={`${state.scope}:${managementMode}`}
        settingsPath={settingsPath}
        managementMode={managementMode}
        showAccountScope={showAccountScope}
      />
    </QueryClientProvider>
  )
  const view = render(tree())
  return {
    ...view,
    refresh: () => view.rerender(tree()),
    changeMode: (managementMode: boolean) =>
      view.rerender(tree(managementMode)),
  }
}

it('lists trusted users without credentials and switches without a User Key', async () => {
  state.serverMode = 'trusted'
  state.apiKey = ''
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'scope.switchUser' }))
  fireEvent.click(await screen.findByRole('button', { name: 'bob' }))
  expect(state.users).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: 'root-key' }),
    'team-a',
    false,
  )
  expect(state.switchIdentity).toHaveBeenCalledWith(
    expect.objectContaining({
      accountId: 'team-a',
      userId: 'bob',
      apiKey: '',
      preserveProfile: true,
    }),
  )
})

it('accepts an unlisted trusted User ID without asking for another key', async () => {
  state.serverMode = 'trusted'
  state.apiKey = ''
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'scope.switchUser' }))
  fireEvent.change(screen.getByRole('textbox', { name: 'scope.searchUsers' }), {
    target: { value: 'new-user' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'scope.useTrustedUser' }))
  expect(state.switchIdentity).toHaveBeenCalledWith(
    expect.objectContaining({
      accountId: 'team-a',
      userId: 'new-user',
      apiKey: '',
    }),
  )
})
it('shows each service once, leaving Account and User details out of its menu', () => {
  mount()
  expect(
    screen.getByRole('button', { name: 'connections.switch' }).textContent,
  ).toBe('Development')
  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  expect(screen.getAllByRole('button', { name: /Development/ })).toHaveLength(1)
  expect(screen.queryByText('Same server, another configuration')).toBeNull()
  expect(within(screen.getByRole('dialog')).queryByText('alice')).toBeNull()
  expect(screen.queryByText('scope.userCredential')).toBeNull()
  expect(screen.queryByText(/team-a\s*\//)).toBeNull()
  expect(
    screen
      .getByRole('button', { name: /Development\s*http/ })
      .getAttribute('aria-current'),
  ).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: /Development\s*http/ }))
  expect(state.activateProfile).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  fireEvent.click(screen.getByRole('button', { name: /Production\s*https/ }))
  expect(state.activateProfile).toHaveBeenCalledWith('three')
  expect(state.accounts).not.toHaveBeenCalled()
  expect(state.users).not.toHaveBeenCalled()
})

it('keeps the same service label and selection when another saved User profile is active', () => {
  state.activeProfileId = 'two'
  mount()
  expect(
    screen.getByRole('button', { name: 'connections.switch' }).textContent,
  ).toBe('Development')
  fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
  expect(
    screen
      .getByRole('button', { name: /Development\s*http/ })
      .getAttribute('aria-current'),
  ).toBe('true')
  fireEvent.click(screen.getByRole('button', { name: /Development\s*http/ }))
  expect(state.activateProfile).not.toHaveBeenCalled()
})

it('keeps a grouped profile User key available in the third selector under its Account', () => {
  state.accountId = 'other'
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'scope.switchUser' }))
  fireEvent.click(screen.getByRole('button', { name: 'jane' }))
  expect(state.switchIdentity).toHaveBeenCalledWith(
    expect.objectContaining({
      accountId: 'other',
      userId: 'jane',
      apiKey: 'jane-key',
      preserveProfile: true,
    }),
  )
  expect(screen.queryByRole('button', { name: 'production-user' })).toBeNull()
  expect(state.activateProfile).not.toHaveBeenCalled()
  expect(state.accounts).not.toHaveBeenCalled()
  expect(state.users).not.toHaveBeenCalled()
})

it('keeps service-wide Account selection explicit in management mode', async () => {
  mount('/admin/settings')
  expect(screen.queryByRole('button', { name: 'scope.switchUser' })).toBeNull()
  expect(screen.getByRole('status').textContent).toBe('scope.rootManagement')
  fireEvent.click(screen.getByRole('button', { name: 'scope.switchAccount' }))
  fireEvent.click(await screen.findByRole('button', { name: 'team-b' }))
  expect(state.accounts).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: 'root-key' }),
  )
  expect(state.switchManagementAccount).toHaveBeenCalledWith('team-b', {
    preserveProfile: true,
  })
  expect(state.users).not.toHaveBeenCalled()
  expect(state.switchIdentity).not.toHaveBeenCalled()
})
it.each(['root', 'user'])(
  'lists only configured User credentials without a user-directory request for %s',
  async (role) => {
    state.role = role
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'scope.switchUser' }))
    expect(screen.getByRole('button', { name: 'alice' })).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'bob' }))
    expect(state.switchIdentity).toHaveBeenCalledWith({
      accountId: 'team-a',
      userId: 'bob',
      apiKey: 'bob-key',
      preserveProfile: true,
      allowLegacyIdentityFallback: true,
    })
    expect(state.users).not.toHaveBeenCalled()
    expect(state.accounts).not.toHaveBeenCalled()
    expect(state.activateProfile).not.toHaveBeenCalled()
  },
)
it('keeps User selection out of management mode entirely', () => {
  mount('/admin/settings')
  expect(screen.queryByRole('button', { name: 'scope.switchUser' })).toBeNull()
  expect(state.users).not.toHaveBeenCalled()
})
it('bounds Account Admin management to its Account without selecting saved User keys', () => {
  state.role = 'admin'
  mount('/admin/settings')
  expect(screen.getByRole('status').textContent).toBe('scope.accountManagement')
  expect(
    screen.queryByRole('button', { name: 'scope.switchAccount' }),
  ).toBeNull()
  expect(
    screen.getByRole('group', { name: 'scope.managementAccount' }).textContent,
  ).toBe('team-a')
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.queryByRole('button', { name: 'other' })).toBeNull()
  expect(screen.queryByRole('button', { name: 'scope.switchUser' })).toBeNull()
  expect(state.accounts).not.toHaveBeenCalled()
  expect(state.users).not.toHaveBeenCalled()
  expect(state.switchIdentity).not.toHaveBeenCalled()
})
it('restores the workbench User selector after a mode round trip without switching credentials', () => {
  const view = mount()
  expect(
    screen.getByRole('button', { name: 'scope.switchUser' }).textContent,
  ).toBe('alice')
  fireEvent.click(screen.getByRole('button', { name: 'scope.switchUser' }))
  view.changeMode(true)
  expect(screen.queryByRole('button', { name: 'scope.switchUser' })).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(screen.getByRole('status').textContent).toBe('scope.rootManagement')
  view.changeMode(false)
  expect(
    screen.getByRole('button', { name: 'scope.switchUser' }).textContent,
  ).toBe('alice')
  expect(screen.queryByRole('status')).toBeNull()
  expect(screen.queryByRole('dialog')).toBeNull()
  expect(state.switchIdentity).not.toHaveBeenCalled()
  expect(state.switchManagementAccount).not.toHaveBeenCalled()
})
it('filters saved users by Account after the scope changes', () => {
  const view = mount()
  fireEvent.click(screen.getByRole('button', { name: 'scope.switchUser' }))
  expect(screen.getByRole('button', { name: 'bob' })).toBeTruthy()
  state.accountId = 'team-b'
  state.scope = 'second'
  state.apiKey = ''
  view.refresh()
  fireEvent.click(screen.getByRole('button', { name: 'scope.switchUser' }))
  expect(screen.getByRole('button', { name: 'carol' })).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'bob' })).toBeNull()
  expect(state.users).not.toHaveBeenCalled()
})
it.each(['root', 'user'])(
  'uses saved Account credentials in the workbench without Root discovery for %s',
  (role) => {
    state.role = role
    mount()
    fireEvent.click(screen.getByRole('button', { name: 'scope.switchAccount' }))
    fireEvent.click(screen.getByRole('button', { name: 'other' }))
    expect(state.switchIdentity).toHaveBeenCalledWith(
      expect.objectContaining({
        accountId: 'other',
        userId: 'jane',
        apiKey: 'jane-key',
        preserveProfile: true,
      }),
    )
    expect(state.accounts).not.toHaveBeenCalled()
    expect(state.users).not.toHaveBeenCalled()
    expect(state.switchManagementAccount).not.toHaveBeenCalled()
  },
)
it('provides configuration rather than fetching identities when no User API Key was saved', () => {
  state.apiKey = ''
  state.profileApiKey = ''
  state.hasHistory = false
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'scope.switchUser' }))
  expect(screen.queryByRole('button', { name: 'alice' })).toBeNull()
  expect(screen.getByText('scope.noSavedUsers')).toBeTruthy()
  expect(
    screen.getByRole('link', { name: 'scope.addUserKey' }).getAttribute('href'),
  ).toBe('/connections/user-key')
  expect(state.users).not.toHaveBeenCalled()
})
it('keeps Account creation in the management selector', async () => {
  mount('/admin/settings')
  fireEvent.click(screen.getByRole('button', { name: 'scope.switchAccount' }))
  await screen.findByRole('button', { name: 'team-b' })
  fireEvent.click(screen.getByRole('button', { name: 'create' }))
  expect(await screen.findByRole('dialog')).toBeTruthy()
})
it.each([
  '/admin/configuration/server',
  '/admin/configuration/server/',
] as const)(
  'omits Account and User controls and directory queries when the page has no Account scope: %s',
  (path) => {
    mount(path, false)
    expect(
      screen.getByRole('button', { name: 'connections.switch' }),
    ).toBeTruthy()
    expect(
      screen.queryByRole('button', { name: 'scope.switchAccount' }),
    ).toBeNull()
    expect(
      screen.queryByRole('group', { name: 'scope.managementAccount' }),
    ).toBeNull()
    expect(
      screen.queryByRole('button', { name: 'scope.switchUser' }),
    ).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'connections.switch' }))
    expect(state.accounts).not.toHaveBeenCalled()
    expect(state.users).not.toHaveBeenCalled()
  },
)
