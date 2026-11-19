// @vitest-environment jsdom
import userEvent from '@testing-library/user-event'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { AdminScopeFilters, useAdminUserScope } from './admin-scope-filters'

const mocks = vi.hoisted(() => ({ users: vi.fn(), health: vi.fn() }))
const state = vi.hoisted(() => ({
  connection: {
    baseUrl: 'http://server.test',
    accountId: 'first',
    userId: 'admin',
    apiKey: '',
    adminApiKey: 'control-key',
  },
  profiles: [] as any[],
  connectionRole: 'root',
  serverMode: 'api_key',
  isConnectionRoleLoading: false,
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => state,
}))
vi.mock('#/features/connection/hooks/use-admin-space-headers', () => ({
  useAdminSpaceHeaders: () => ({
    headers: { 'X-API-Key': 'space-admin-key' },
    scopeKey: state.connection.accountId,
    isLoading: false,
    unavailableKey: 'scopeUnavailable',
  }),
}))
vi.mock('#/shared/api/admin', () => ({ fetchAdminUsers: mocks.users }))
vi.mock('#/features/connection/lib/health', () => ({
  fetchServerHealth: mocks.health,
  readServerIdentity: (health: any) => ({
    accountId: health.account_id,
    userId: health.user_id,
  }),
}))
vi.mock('./user-space-switcher', () => ({
  UserSpaceSwitcher: () => <span>space</span>,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

const clients: QueryClient[] = []
afterEach(() => {
  cleanup()
  clients.splice(0).forEach((client) => client.clear())
})
beforeEach(() => {
  state.connection.accountId = 'first'
  state.connectionRole = 'root'
  state.serverMode = 'api_key'
  state.profiles = []
  mocks.users.mockReset().mockResolvedValue([
    { userId: 'alice', accountId: 'first', role: 'user', apiKey: 'alice-key' },
    { userId: 'bob', accountId: 'first', role: 'user', apiKey: '' },
    {
      userId: 'admin',
      accountId: 'first',
      role: 'admin',
      apiKey: 'space-admin-key',
    },
  ])
  mocks.health
    .mockReset()
    .mockResolvedValue({ account_id: 'first', user_id: 'alice', role: 'user' })
})
function mount(management = true, kind: 'usage' | 'tasks' = 'usage') {
  function Probe() {
    const scope = useAdminUserScope(management, kind)
    return (
      <>
        <AdminScopeFilters userFilter={scope.userFilter} />
        <output data-testid="headers">{JSON.stringify(scope.headers)}</output>
        <output data-testid="scope">{scope.scopeKey}</output>
      </>
    )
  }
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  clients.push(client)
  const tree = () => (
    <QueryClientProvider client={client}>
      <Probe />
    </QueryClientProvider>
  )
  const view = render(tree())
  return { ...view, refresh: () => view.rerender(tree()) }
}
async function openUsers() {
  fireEvent.click(
    screen.getByRole('combobox', { name: 'adminNavigation.userFilter' }),
  )
  return await screen.findByRole('option', { name: 'alice' })
}
it('validates a selected User and isolates requests without changing management credentials', async () => {
  mount()
  expect(mocks.users).not.toHaveBeenCalled()
  await userEvent.click(await openUsers())
  await waitFor(() =>
    expect(screen.getByTestId('headers').textContent).toBe(
      '{"X-API-Key":"alice-key"}',
    ),
  )
  expect(screen.getByTestId('scope').textContent).toBe('first/user/alice')
  expect(state.connection.adminApiKey).toBe('control-key')
  expect(state.connection.userId).toBe('admin')
  await openUsers()
  await userEvent.click(
    screen.getAllByRole('option', { name: 'adminNavigation.allUsers' }).at(-1)!,
  )
  expect(screen.getByTestId('headers').textContent).toBe(
    '{"X-API-Key":"space-admin-key"}',
  )
})
it('allows an Account Admin to select Users but disables masked credentials', async () => {
  state.connectionRole = 'admin'
  mount()
  await openUsers()
  expect(
    screen
      .getByRole('option', { name: /bob/ })
      .getAttribute('aria-disabled') === 'true',
  ).toBe(true)
  expect(screen.queryByRole('option', { name: 'admin' })).toBeNull()
  await userEvent.click(screen.getByRole('option', { name: 'alice' }))
  await waitFor(() =>
    expect(screen.getByTestId('headers').textContent).toContain('alice-key'),
  )
})
it('rejects a saved key whose verified user differs from the selection', async () => {
  mocks.health.mockResolvedValue({
    account_id: 'first',
    user_id: 'bob',
    role: 'user',
  })
  mount()
  await userEvent.click(await openUsers())
  await waitFor(() => expect(mocks.health).toHaveBeenCalled())
  await waitFor(() =>
    expect(screen.getByTestId('headers').textContent).toBe('null'),
  )
})
it('drops the selected User immediately when the account changes', async () => {
  const view = mount()
  await userEvent.click(await openUsers())
  await waitFor(() =>
    expect(screen.getByTestId('headers').textContent).toContain('alice-key'),
  )
  state.connection.accountId = 'second'
  view.refresh()
  expect(screen.getByTestId('scope').textContent).toBe('second')
  expect(screen.getByTestId('headers').textContent).not.toContain('alice-key')
})
it('supports trusted User assertions for Account Admin without requiring plaintext User keys', async () => {
  state.connectionRole = 'admin'
  state.serverMode = 'trusted'
  mount()
  await openUsers()
  expect(
    screen
      .getByRole('option', { name: 'bob' })
      .getAttribute('aria-disabled') === 'true',
  ).toBe(false)
  await userEvent.click(screen.getByRole('option', { name: 'alice' }))
  await waitFor(() =>
    expect(screen.getByTestId('headers').textContent).toContain(
      'X-OpenViking-User',
    ),
  )
  expect(mocks.health).toHaveBeenCalledWith(
    'http://server.test',
    expect.objectContaining({
      'X-OpenViking-Account': 'first',
      'X-OpenViking-User': 'alice',
      'X-OpenViking-Role': 'user',
    }),
    expect.any(AbortSignal),
  )
})
it('does not offer a User filter or query the directory in the workbench', () => {
  mount(false)
  expect(
    screen.queryByRole('combobox', { name: 'adminNavigation.userFilter' }),
  ).toBeNull()
  expect(mocks.users).not.toHaveBeenCalled()
})

it('stops using cached User credentials when management permission is lost', async () => {
  const view = mount()
  await userEvent.click(await openUsers())
  await waitFor(() =>
    expect(screen.getByTestId('headers').textContent).toContain('alice-key'),
  )
  state.connectionRole = 'user'
  view.refresh()
  expect(screen.getByTestId('headers').textContent).toBe('null')
  expect(
    screen.queryByRole('combobox', { name: 'adminNavigation.userFilter' }),
  ).toBeNull()
})
