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
import { UserSpaceSwitcher } from './user-space-switcher'

const mocks = vi.hoisted(() => ({
  accounts: vi.fn(),
  switchAccount: vi.fn(),
  role: 'root',
  key: 'root-key',
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: {
      accountId: 'first',
      baseUrl: 'http://server.test',
      apiKey: 'data-key',
      adminApiKey: mocks.key,
    },
    identityScopeKey: 'root',
    connectionRole: mocks.role,
    isConnectionRoleLoading: false,
    switchManagementAccount: mocks.switchAccount,
  }),
}))
vi.mock('#/shared/api/admin', () => ({ fetchAdminAccounts: mocks.accounts }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
beforeEach(() => {
  mocks.role = 'root'
  mocks.key = 'root-key'
  mocks.accounts.mockResolvedValue([
    { accountId: 'first' },
    { accountId: 'second' },
  ])
  mocks.switchAccount.mockResolvedValue(undefined)
})
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
function mount(canManageAccounts: boolean) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <UserSpaceSwitcher canManageAccounts={canManageAccounts} />
    </QueryClientProvider>,
  )
}
it('keeps the current space read-only for an account administrator', () => {
  mount(false)
  expect(screen.getByText('first')).toBeTruthy()
  expect(screen.queryByRole('combobox')).toBeNull()
  expect(mocks.accounts).not.toHaveBeenCalled()
})
it('loads spaces on demand with the management key and switches the management scope', async () => {
  mount(true)
  expect(mocks.accounts).not.toHaveBeenCalled()
  fireEvent.click(
    screen.getByRole('combobox', { name: 'adminNavigation.currentSpace' }),
  )
  await userEvent.click(await screen.findByRole('option', { name: 'second' }))
  await waitFor(() =>
    expect(mocks.switchAccount).toHaveBeenCalledWith('second', {
      preserveProfile: true,
    }),
  )
  expect(mocks.accounts).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: 'root-key' }),
    expect.any(AbortSignal),
  )
})

it.each(['admin', 'missing-root-key'])(
  'does not offer cross-account selection for %s',
  (mode) => {
    if (mode === 'admin') mocks.role = 'admin'
    else mocks.key = ''
    mount(true)
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(mocks.accounts).not.toHaveBeenCalled()
  },
)
