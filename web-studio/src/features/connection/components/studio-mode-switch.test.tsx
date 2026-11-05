import i18n from '#/i18n/core'
// @vitest-environment jsdom
import { StudioModeSwitch } from '#/features/connection/components/studio-mode-switch'
import type { ConnectionProfile } from '#/features/connection/lib/profiles'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  connection: {
    baseUrl: 'http://service.test',
    serviceSelection: 'opensource',
    accountId: 'team',
    userId: '',
    apiKey: '',
    adminApiKey: 'root-key',
  },
  profiles: [] as ConnectionProfile[],
  switch: vi.fn(),
  navigate: vi.fn(),
  flush: vi.fn(),
  error: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection: mocks.connection,
    profiles: mocks.profiles,
    serverMode: 'api_key',
    switchIdentity: mocks.switch,
  }),
}))
vi.mock('#/features/connection/lib/credential-storage', () => ({
  flushCredentialStorage: mocks.flush,
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
  Link: ({
    to,
    search,
    children,
  }: {
    to: string
    search?: { returnTo?: string }
    children: React.ReactNode
  }) => (
    <a
      href={
        to +
        (search?.returnTo
          ? `?returnTo=${encodeURIComponent(search.returnTo)}`
          : '')
      }
    >
      {children}
    </a>
  ),
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('sonner', () => ({ toast: { error: mocks.error } }))
beforeEach(() => {
  vi.clearAllMocks()
  mocks.connection.apiKey = ''
  mocks.connection.userId = ''
  mocks.profiles = []
  mocks.switch.mockResolvedValue(undefined)
  mocks.flush.mockResolvedValue(undefined)
})
afterEach(cleanup)
function profile(userId: string, accountId = 'team'): ConnectionProfile {
  return {
    id: `${accountId}/${userId}`,
    name: userId,
    connection: {
      ...mocks.connection,
      serviceSelection: 'opensource',
      accountId,
      userId,
      apiKey: `${userId}-key`,
    },
  }
}
it('always calls the entry Enter Workbench and routes a Root-only connection with no saved User to contextual key setup', () => {
  render(<StudioModeSwitch managementMode />)
  expect(
    screen
      .getByRole('link', { name: 'modes.enterWorkbench' })
      .getAttribute('href'),
  ).toBe('/connections/user-key?returnTo=%2Fhome')
  expect(screen.queryByText('modes.selectUser')).toBeNull()
})
it('enters directly when a User key is already active', () => {
  mocks.connection.apiKey = 'alice-key'
  mocks.connection.userId = 'alice'
  render(<StudioModeSwitch managementMode />)
  expect(
    screen
      .getByRole('link', { name: 'modes.enterWorkbench' })
      .getAttribute('href'),
  ).toBe('/home')
  expect(mocks.switch).not.toHaveBeenCalled()
})
it('restores the single saved User automatically, retaining Root and waiting for encrypted persistence before navigation', async () => {
  mocks.profiles = [profile('alice')]
  let resolve!: () => void
  mocks.flush.mockReturnValue(
    new Promise<void>((done) => {
      resolve = done
    }),
  )
  render(<StudioModeSwitch managementMode />)
  fireEvent.click(screen.getByRole('button', { name: 'modes.enterWorkbench' }))
  await waitFor(() => expect(mocks.flush).toHaveBeenCalledOnce())
  expect(mocks.switch).toHaveBeenCalledWith(
    expect.objectContaining({
      accountId: 'team',
      userId: 'alice',
      apiKey: 'alice-key',
      preserveProfile: true,
      signal: expect.any(AbortSignal),
    }),
  )
  expect(mocks.connection.adminApiKey).toBe('root-key')
  expect(mocks.navigate).not.toHaveBeenCalled()
  resolve()
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/home' }),
  )
})
it('lets the user choose among local User credentials from the current Account only', async () => {
  mocks.profiles = [
    profile('alice'),
    profile('bob'),
    profile('carol', 'another-account'),
  ]
  render(<StudioModeSwitch managementMode />)
  fireEvent.click(screen.getByRole('button', { name: 'modes.enterWorkbench' }))
  expect(screen.getByRole('dialog')).toBeTruthy()
  expect(screen.queryByRole('button', { name: 'carol' })).toBeNull()
  fireEvent.click(screen.getByRole('button', { name: 'bob' }))
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/home' }),
  )
  expect(mocks.switch).toHaveBeenCalledWith(
    expect.objectContaining({ userId: 'bob', apiKey: 'bob-key' }),
  )
})
it('does not enter the workbench when the saved key is rejected', async () => {
  mocks.profiles = [profile('alice')]
  mocks.switch.mockRejectedValue(new Error('invalid-key'))
  render(<StudioModeSwitch managementMode />)
  fireEvent.click(screen.getByRole('button', { name: 'modes.enterWorkbench' }))
  await waitFor(() =>
    expect(mocks.error).toHaveBeenCalledWith(
      i18n.t('studio:errors.requestFailed'),
      { description: 'invalid-key' },
    ),
  )
  expect(mocks.navigate).not.toHaveBeenCalled()
})
it('aborts a pending identity restoration when the control unmounts', () => {
  mocks.profiles = [profile('alice')]
  mocks.switch.mockReturnValue(new Promise(() => {}))
  const view = render(<StudioModeSwitch managementMode />)
  fireEvent.click(screen.getByRole('button', { name: 'modes.enterWorkbench' }))
  const signal = mocks.switch.mock.calls[0][0].signal as AbortSignal
  view.unmount()
  expect(signal.aborted).toBe(true)
  expect(mocks.navigate).not.toHaveBeenCalled()
})
it('retains the Return to Management entry in the workbench', () => {
  render(<StudioModeSwitch managementMode={false} />)
  expect(
    screen
      .getByRole('link', { name: 'modes.returnManagement' })
      .getAttribute('href'),
  ).toBe('/admin')
})
