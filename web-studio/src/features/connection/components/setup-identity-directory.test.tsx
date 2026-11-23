// @vitest-environment jsdom
import { SetupIdentityDirectory } from '#/features/connection/components/setup-identity-directory'
import { act, cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  health: vi.fn(),
  accounts: vi.fn(),
  users: vi.fn(),
}))
vi.mock('#/features/connection/lib/health', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  fetchConnectionHealth: mocks.health,
}))
vi.mock('#/shared/api/admin', () => ({
  fetchAdminAccounts: mocks.accounts,
  fetchAdminUserDirectory: mocks.users,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
const connection = {
  serviceSelection: 'opensource' as const,
  baseUrl: 'https://service.test',
  apiKey: 'user-secret',
  adminApiKey: 'root-secret',
  accountId: 'team',
  userId: 'alice',
}
beforeEach(() => {
  vi.resetAllMocks()
  mocks.accounts.mockResolvedValue([
    { accountId: 'team', userCount: 1 },
    { accountId: 'other', userCount: 1 },
  ])
  mocks.users.mockResolvedValue([
    { accountId: 'team', userId: 'alice', role: 'user' },
  ])
})
afterEach(cleanup)
it('loads root accounts and a credential-free user directory without changing identity on load', async () => {
  mocks.health.mockResolvedValue({
    version: 'test',
    role: 'root',
    auth_mode: 'api_key',
    account_id: 'default',
    user_id: 'default',
  })
  const onSelect = vi.fn()
  render(
    <SetupIdentityDirectory
      connection={connection}
      disabled={false}
      onSelect={onSelect}
    />,
  )
  await screen.findByRole('combobox', { name: 'directoryAccount' })
  await waitFor(() => expect(mocks.users).toHaveBeenCalled())
  expect(mocks.health).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: '', adminApiKey: 'root-secret' }),
    expect.any(AbortSignal),
    'control',
  )
  expect(mocks.users).toHaveBeenCalledWith(
    expect.objectContaining({ apiKey: 'root-secret' }),
    'team',
  )
  expect(onSelect).not.toHaveBeenCalled()
  const user = userEvent.setup()
  await waitFor(() =>
    expect(
      screen
        .getByRole('combobox', { name: 'directoryAccount' })
        .hasAttribute('disabled'),
    ).toBe(false),
  )
  await user.click(screen.getByRole('combobox', { name: 'directoryAccount' }))
  await user.click(await screen.findByRole('option', { name: 'other' }))
  expect(onSelect).toHaveBeenCalledWith({ accountId: 'other', userId: '' })
})
it('restricts an admin key to its authoritative account instead of listing all accounts', async () => {
  mocks.health.mockResolvedValue({
    version: 'test',
    role: 'admin',
    account_id: 'bound-account',
    user_id: 'default',
  })
  const onSelect = vi.fn()
  render(
    <SetupIdentityDirectory
      connection={connection}
      disabled={false}
      onSelect={onSelect}
    />,
  )
  await screen.findByRole('combobox', { name: 'directoryAccount' })
  expect(mocks.accounts).not.toHaveBeenCalled()
  expect(mocks.users).not.toHaveBeenCalled()
  const user = userEvent.setup()
  await waitFor(() =>
    expect(
      screen
        .getByRole('combobox', { name: 'directoryAccount' })
        .hasAttribute('disabled'),
    ).toBe(false),
  )
  await user.click(screen.getByRole('combobox', { name: 'directoryAccount' }))
  expect(screen.queryByRole('option', { name: 'team' })).toBeNull()
  await user.click(await screen.findByRole('option', { name: 'bound-account' }))
  expect(mocks.users).toHaveBeenCalledWith(
    expect.objectContaining({ accountId: 'bound-account' }),
    'bound-account',
  )
})
it('discards directory results after the control key changes', async () => {
  let resolveOld!: (value: unknown) => void
  mocks.health
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveOld = resolve
        }),
    )
    .mockResolvedValueOnce({ version: 'test', role: 'user' })
  const { rerender } = render(
    <SetupIdentityDirectory
      connection={connection}
      disabled={false}
      onSelect={vi.fn()}
    />,
  )
  rerender(
    <SetupIdentityDirectory
      connection={{ ...connection, adminApiKey: 'new-key' }}
      disabled={false}
      onSelect={vi.fn()}
    />,
  )
  await screen.findByText('directoryControlRequired')
  await act(async () => resolveOld({ version: 'test', role: 'root' }))
  expect(mocks.accounts).not.toHaveBeenCalled()
  expect(screen.queryByRole('combobox')).toBeNull()
})

it.each(['user', 'admin'])(
  'loads global accounts for a trusted Root key when the data role is %s',
  async (role) => {
    mocks.health.mockResolvedValue({
      version: 'test',
      auth_mode: 'trusted',
      role,
      account_id: 'team',
      user_id: 'alice',
    })
    const onSelect = vi.fn()
    render(
      <SetupIdentityDirectory
        connection={connection}
        disabled={false}
        onSelect={onSelect}
      />,
    )
    await waitFor(() =>
      expect(mocks.users).toHaveBeenCalledWith(
        expect.objectContaining({ apiKey: 'root-secret' }),
        'team',
      ),
    )
    expect(onSelect).not.toHaveBeenCalled()
    expect(mocks.accounts).toHaveBeenCalledWith(
      expect.objectContaining({ apiKey: 'root-secret' }),
    )
    const user = userEvent.setup()
    await waitFor(() =>
      expect(
        screen
          .getByRole('combobox', { name: 'directoryAccount' })
          .hasAttribute('disabled'),
      ).toBe(false),
    )
    await user.click(screen.getByRole('combobox', { name: 'directoryAccount' }))
    await user.click(await screen.findByRole('option', { name: 'other' }))
    expect(onSelect).toHaveBeenCalledWith({ accountId: 'other', userId: '' })
    await waitFor(() =>
      expect(mocks.users).toHaveBeenLastCalledWith(
        expect.objectContaining({ apiKey: 'root-secret', accountId: 'other' }),
        'other',
      ),
    )
  },
)

it.each([403, 503])(
  'does not infer control permissions from a trusted admin data role when the account probe fails with %s',
  async (status) => {
    mocks.health.mockResolvedValue({
      version: 'test',
      auth_mode: 'trusted',
      role: 'admin',
      account_id: 'team',
      user_id: 'alice',
    })
    mocks.accounts.mockRejectedValue({ response: { status } })
    const onSelect = vi.fn()
    render(
      <SetupIdentityDirectory
        connection={connection}
        disabled={false}
        onSelect={onSelect}
      />,
    )
    await screen.findByText(/directoryLoadFailed/)
    expect(screen.queryByRole('combobox')).toBeNull()
    expect(mocks.users).not.toHaveBeenCalled()
    expect(onSelect).not.toHaveBeenCalled()
  },
)

it('shows useful directory failure details without exposing either credential', async () => {
  mocks.health.mockResolvedValue({
    version: 'test',
    auth_mode: 'api_key',
    role: 'root',
  })
  mocks.accounts.mockRejectedValue(
    new Error('Service unavailable for root-secret and user-secret'),
  )
  render(
    <SetupIdentityDirectory
      connection={{
        ...connection,
        apiKey: ' user-secret ',
        adminApiKey: ' root-secret ',
      }}
      disabled={false}
      onSelect={vi.fn()}
    />,
  )
  const alert = await screen.findByRole('alert')
  expect(alert.textContent).toContain('directoryLoadFailed')
  expect(alert.textContent).toContain('Service unavailable')
  expect(alert.textContent).not.toContain('root-secret')
  expect(alert.textContent).not.toContain('user-secret')
})
