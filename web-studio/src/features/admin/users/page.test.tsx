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
import { afterEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  create: vi.fn(),
  switchIdentity: vi.fn(),
  result: vi.fn(),
  navigate: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      baseUrl: 'http://ov.test',
      accountId: 'team',
      userId: 'manager',
      apiKey: 'my-key',
      adminApiKey: 'admin-key',
    },
    connectionRole: 'admin',
    serverMode: 'api_key',
    isConnectionRoleLoading: false,
    setGeneratedCredential: mocks.result,
    switchIdentity: mocks.switchIdentity,
  }),
}))
vi.mock('#/shared/api/admin', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  createAdminUser: mocks.create,
  fetchAdminUsersPage: async () => ({
    users: [],
    total: 0,
    accountTotal: 0,
    managerCount: 1,
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
