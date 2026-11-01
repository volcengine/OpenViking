// @vitest-environment jsdom
import { UserManagementPanel } from '#/features/admin/users/page'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import type { AdminUser } from '#/shared/api/admin'

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  switchIdentity: vi.fn(),
  result: vi.fn(),
  navigate: vi.fn(),
  connection: {
    baseUrl: 'http://ov.test',
    accountId: 'team',
    userId: 'manager',
    apiKey: 'my-key',
    adminApiKey: 'admin-key',
  },
  connectionRole: 'admin',
  serverMode: 'api_key',
  users: [] as AdminUser[],
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: mocks.connection,
    connectionRole: mocks.connectionRole,
    serverMode: mocks.serverMode,
    isConnectionRoleLoading: false,
    setGeneratedCredential: mocks.result,
    switchIdentity: mocks.switchIdentity,
  }),
}))
vi.mock('#/shared/api/admin', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  createAdminUser: mocks.create,
  fetchAdminUsersPage: async () => ({
    users: mocks.users,
    total: mocks.users.length,
    accountTotal: mocks.users.length,
    managerCount: 2,
    keyCount: 0,
  }),
  fetchAdminGroups: async () => [],
}))
vi.mock('#/features/admin/users/components/add-user-dialog', () => ({
  AddUserDialog: ({
    open,
    onCreate,
  }: {
    open: boolean
    onCreate: (input: unknown) => void
  }) =>
    open ? (
      <button
        onClick={() =>
          onCreate({ accountId: 'team', userId: 'teammate', role: 'user' })
        }
      >
        confirm-create
      </button>
    ) : null,
}))
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
  useNavigate: () => mocks.navigate,
  Outlet: () => null,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
beforeEach(() => {
  mocks.connection.apiKey = 'my-key'
  mocks.connectionRole = 'admin'
  mocks.serverMode = 'api_key'
  mocks.users = []
})

it.each([
  ['Root-only', 'root', 'api_key', '', false],
  ['Account Admin data key', 'admin', 'api_key', 'my-key', true],
  ['trusted asserted user', 'root', 'trusted', '', true],
] as const)(
  'marks and protects only an actual data identity for %s',
  async (_, role, serverMode, apiKey, current) => {
    mocks.connectionRole = role
    mocks.serverMode = serverMode
    mocks.connection.apiKey = apiKey
    mocks.users = [
      {
        accountId: 'team',
        userId: 'manager',
        role: 'user',
        apiKey: 'member-key',
      },
    ]
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    render(
      <QueryClientProvider client={client}>
        <UserManagementPanel />
      </QueryClientProvider>,
    )
    await screen.findByText('manager')
    expect(Boolean(screen.queryByText('actions.currentIdentity'))).toBe(current)
    expect(
      screen
        .getByRole('button', { name: 'actions.removeUser' })
        .hasAttribute('disabled'),
    ).toBe(current)
    expect(
      Boolean(screen.queryByRole('button', { name: 'actions.switchIdentity' })),
    ).toBe(!current)
    if (!current) {
      fireEvent.click(
        screen.getByRole('button', { name: 'actions.switchIdentity' }),
      )
      await waitFor(() =>
        expect(mocks.switchIdentity).toHaveBeenCalledWith(
          expect.objectContaining({ userId: 'manager', apiKey: 'member-key' }),
        ),
      )
    }
    client.clear()
  },
)
it('creates a teammate and shows their credential without replacing the administrator data identity or navigating', async () => {
  const result = {
    accountId: 'team',
    userId: 'teammate',
    apiKey: 'teammate-key',
  }
  mocks.create.mockResolvedValue(result)
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(
    <QueryClientProvider client={client}>
      <UserManagementPanel />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: 'actions.addUser' }))
  fireEvent.click(screen.getByRole('button', { name: 'confirm-create' }))
  await waitFor(() => expect(mocks.result).toHaveBeenCalledWith(result))
  expect(mocks.switchIdentity).not.toHaveBeenCalled()
  expect(mocks.navigate).not.toHaveBeenCalled()
  expect(mocks.create).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: 'admin-key' }),
    expect.objectContaining({ userId: 'teammate' }),
  )
  client.clear()
})
