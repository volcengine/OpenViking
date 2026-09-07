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

import { CurrentUserMenu } from './current-user-menu'

const adminMocks = vi.hoisted(() => ({
  fetchAdminUsers: vi.fn(),
}))

const connectionMocks = vi.hoisted(() => ({
  connection: {
    accountId: 'account-a',
    adminApiKey: 'root-key',
    apiKey: '',
    baseUrl: 'http://localhost:1933',
    userId: 'alice',
  },
  serverMode: 'trusted',
  switchIdentity: vi.fn(),
}))

const toastMocks = vi.hoisted(() => ({
  error: vi.fn(),
  success: vi.fn(),
}))

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => key,
  }),
}))

vi.mock('sonner', () => ({ toast: toastMocks }))

vi.mock('#/hooks/use-app-connection', () => ({
  useAppConnection: () => connectionMocks,
}))

vi.mock('#/lib/admin', () => adminMocks)

function renderMenu() {
  const queryClient = new QueryClient({
    defaultOptions: {
      mutations: { retry: false },
      queries: { retry: false },
    },
  })

  return render(
    <QueryClientProvider client={queryClient}>
      <CurrentUserMenu />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  connectionMocks.connection.adminApiKey = 'root-key'
  connectionMocks.serverMode = 'trusted'
  connectionMocks.switchIdentity.mockResolvedValue(undefined)
  adminMocks.fetchAdminUsers.mockResolvedValue([
    {
      accountId: 'account-a',
      role: 'admin',
      userId: 'alice',
    },
    {
      accountId: 'account-a',
      role: 'user',
      userId: 'bob',
    },
  ])
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('CurrentUserMenu', () => {
  it('switches trusted identities without requiring a user API key', async () => {
    renderMenu()

    fireEvent.click(
      screen.getByRole('button', { name: 'header.currentUser.openMenu' }),
    )

    const bob = await screen.findByRole('button', { name: 'bob' })
    fireEvent.click(bob)

    await waitFor(() => {
      expect(connectionMocks.switchIdentity).toHaveBeenCalledWith({
        accountId: 'account-a',
        allowLegacyIdentityFallback: true,
        apiKey: '',
        userId: 'bob',
      })
    })
    expect(toastMocks.success).toHaveBeenCalledWith(
      'header.currentUser.switchSuccess',
    )
  })

  it('keeps non-trusted user menus read-only', () => {
    connectionMocks.serverMode = 'api_key'
    renderMenu()

    fireEvent.click(
      screen.getByRole('button', { name: 'header.currentUser.openMenu' }),
    )

    expect(screen.queryByText('header.currentUser.switchUser')).toBeNull()
    expect(adminMocks.fetchAdminUsers).not.toHaveBeenCalled()
  })

  it('accepts a user ID when trusted mode has no user directory', async () => {
    connectionMocks.connection.adminApiKey = ''
    renderMenu()

    fireEvent.click(
      screen.getByRole('button', { name: 'header.currentUser.openMenu' }),
    )
    fireEvent.change(
      screen.getByPlaceholderText('header.currentUser.userIdPlaceholder'),
      { target: { value: ' bob ' } },
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'header.currentUser.switchAction' }),
    )

    await waitFor(() => {
      expect(connectionMocks.switchIdentity).toHaveBeenCalledWith({
        accountId: 'account-a',
        allowLegacyIdentityFallback: true,
        apiKey: '',
        userId: 'bob',
      })
    })
    expect(adminMocks.fetchAdminUsers).not.toHaveBeenCalled()
  })
})
