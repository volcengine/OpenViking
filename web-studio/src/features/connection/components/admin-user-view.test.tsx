// @vitest-environment jsdom
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import i18n from '#/i18n'
import { AdminUserView } from './admin-user-view'

const mocks = vi.hoisted(() => ({
  state: {
    serverMode: 'api_key',
    identityScopeKey: 'scope-1',
    connection: {
      baseUrl: 'http://ov.test',
      accountId: 'acme',
      userId: 'admin',
      apiKey: '',
      adminApiKey: 'synthetic-admin',
    },
    serverHealth: {
      role: 'admin',
      capabilities: { admin_user_delegation: true },
    },
  },
  save: vi.fn(),
  activate: vi.fn(),
  navigate: vi.fn(),
  health: vi.fn(),
  flush: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    ...mocks.state,
    saveProfile: mocks.save,
    activateProfile: mocks.activate,
  }),
}))
vi.mock('#/features/connection/lib/health', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  fetchServerHealth: mocks.health,
}))
vi.mock('#/features/connection/lib/credential-storage', () => ({
  flushCredentialStorage: mocks.flush,
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => mocks.navigate }))
beforeEach(async () => {
  vi.clearAllMocks()
  mocks.state.serverHealth.capabilities.admin_user_delegation = true
  mocks.state.identityScopeKey = 'scope-1'
  mocks.health.mockResolvedValue({})
  mocks.flush.mockResolvedValue(undefined)
  await i18n.changeLanguage('en')
})
afterEach(cleanup)

it('offers the action only when the current server advertises the explicit capability', () => {
  mocks.state.serverHealth.capabilities.admin_user_delegation = false
  render(<AdminUserView />)
  expect(screen.queryByRole('button', { name: 'Open user view' })).toBeNull()
  expect(mocks.health).not.toHaveBeenCalled()
})

it('verifies and persists a separate USER profile before navigation without copying a target key', async () => {
  let finish!: () => void
  mocks.flush.mockReturnValue(
    new Promise<void>((resolve) => {
      finish = resolve
    }),
  )
  render(<AdminUserView />)
  fireEvent.change(screen.getByLabelText('Existing user ID'), {
    target: { value: 'alice' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Open user view' }))
  await waitFor(() => expect(mocks.flush).toHaveBeenCalled())
  expect(mocks.health).toHaveBeenCalledWith('http://ov.test', {
    Authorization: 'OpenViking-Delegate synthetic-admin',
    'X-OpenViking-Account': 'acme',
    'X-OpenViking-Delegate-User': 'alice',
  })
  expect(mocks.save).toHaveBeenCalledWith(
    expect.objectContaining({
      connection: expect.objectContaining({
        apiKey: '',
        adminApiKey: 'synthetic-admin',
        userId: 'alice',
        adminDelegation: true,
      }),
    }),
  )
  expect(mocks.activate).not.toHaveBeenCalled()
  finish()
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/home' }),
  )
})

it('keeps an unsupported server failure visible without saving or activating a profile', async () => {
  mocks.health.mockRejectedValue(new Error('Synthetic capability mismatch'))
  render(<AdminUserView />)
  fireEvent.change(screen.getByLabelText('Existing user ID'), {
    target: { value: 'alice' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Open user view' }))
  expect((await screen.findByRole('alert')).textContent).toContain(
    'Synthetic capability mismatch',
  )
  expect(mocks.save).not.toHaveBeenCalled()
  expect(mocks.activate).not.toHaveBeenCalled()
})

it('rejects a stale verification after the active connection changes', async () => {
  let finish!: () => void
  mocks.health.mockReturnValue(
    new Promise<void>((resolve) => {
      finish = resolve
    }),
  )
  const view = render(<AdminUserView />)
  fireEvent.change(screen.getByLabelText('Existing user ID'), {
    target: { value: 'alice' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'Open user view' }))
  mocks.state.identityScopeKey = 'scope-2'
  view.rerender(<AdminUserView />)
  finish()
  await screen.findByRole('alert')
  expect(mocks.save).not.toHaveBeenCalled()
})
