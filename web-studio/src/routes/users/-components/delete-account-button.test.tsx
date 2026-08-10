// @vitest-environment jsdom

import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { DeleteAccountButton } from './delete-account-button'

const adminMocks = vi.hoisted(() => ({
  deleteAdminAccount: vi.fn(),
  fetchAdminAccounts: vi.fn(),
}))

const connectionMocks = vi.hoisted(() => ({
  openConnectionSettings: vi.fn(),
  switchManagementAccount: vi.fn(),
}))

const toastMocks = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
  warning: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))

vi.mock('sonner', () => ({ toast: toastMocks }))

vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => connectionMocks,
}))

vi.mock('#/lib/admin', () => adminMocks)

const adminConnection = {
  accountId: 'account-a',
  apiKey: 'root-key',
  baseUrl: 'http://localhost:1933',
  userId: 'root',
}

function renderButton() {
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <DeleteAccountButton
        accountId="account-a"
        adminConnection={adminConnection}
      />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  adminMocks.deleteAdminAccount.mockResolvedValue(undefined)
  adminMocks.fetchAdminAccounts.mockResolvedValue([
    { accountId: 'account-b', userCount: 1 },
  ])
  connectionMocks.switchManagementAccount.mockResolvedValue(undefined)
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('DeleteAccountButton', () => {
  it('requires the exact account name before deleting', async () => {
    renderButton()

    fireEvent.click(
      screen.getByRole('button', { name: 'actions.deleteAccount' }),
    )

    const confirmButton = screen.getByRole<HTMLButtonElement>('button', {
      name: 'actions.confirmDeleteAccount',
    })
    const confirmationInput = screen.getByPlaceholderText('account-a')

    expect(confirmButton.disabled).toBe(true)
    fireEvent.change(confirmationInput, { target: { value: 'account-b' } })
    expect(confirmButton.disabled).toBe(true)

    fireEvent.change(confirmationInput, { target: { value: 'account-a' } })
    expect(confirmButton.disabled).toBe(false)
    fireEvent.click(confirmButton)

    await waitFor(() => {
      expect(adminMocks.deleteAdminAccount).toHaveBeenCalledWith(
        adminConnection,
        'account-a',
      )
    })
    expect(connectionMocks.switchManagementAccount).toHaveBeenCalledWith(
      'account-b',
    )
    expect(toastMocks.success).toHaveBeenCalledWith('toast.accountDeleted')
  })

  it('clears the deleted identity and opens settings when no account remains', async () => {
    adminMocks.fetchAdminAccounts.mockResolvedValue([])
    renderButton()

    fireEvent.click(
      screen.getByRole('button', { name: 'actions.deleteAccount' }),
    )
    fireEvent.change(screen.getByPlaceholderText('account-a'), {
      target: { value: 'account-a' },
    })
    fireEvent.click(
      screen.getByRole('button', { name: 'actions.confirmDeleteAccount' }),
    )

    await waitFor(() => {
      expect(connectionMocks.switchManagementAccount).toHaveBeenCalledWith('')
    })
    expect(connectionMocks.openConnectionSettings).toHaveBeenCalledOnce()
  })
})
