// @vitest-environment jsdom
import { UserKeyPage } from '#/features/connection/pages/-user-key-page'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  verify: vi.fn(),
  switch: vi.fn(),
  navigate: vi.fn(),
  flush: vi.fn(),
  success: vi.fn(),
  search: {} as { returnTo?: '/home' },
}))
const connection = {
  baseUrl: 'https://current.test',
  serviceSelection: 'opensource',
  accountId: 'team',
  userId: 'alice',
  adminApiKey: 'root-key',
  apiKey: 'alice-key',
}
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    connection,
    switchIdentity: mocks.switch,
    activeProfileId: 'profile',
  }),
}))
vi.mock('#/features/connection/lib/user-key', async (original) => ({
  ...(await original<Record<string, unknown>>()),
  verifyUserKey: mocks.verify,
}))
vi.mock('#/features/connection/lib/credential-storage', () => ({
  flushCredentialStorage: mocks.flush,
}))
vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
  useSearch: () => mocks.search,
  Link: ({ children }: { children: React.ReactNode }) => <a>{children}</a>,
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
vi.mock('sonner', () => ({ toast: { success: mocks.success, error: vi.fn() } }))
beforeEach(() => {
  vi.clearAllMocks()
  mocks.search = {}
  mocks.verify.mockResolvedValue({
    ...connection,
    apiKey: 'bob-key',
    userId: 'bob',
  })
  mocks.flush.mockResolvedValue(undefined)
  mocks.switch.mockResolvedValue(undefined)
})
afterEach(cleanup)
it('uses the current service and Account with only a password input and no unlock controls', () => {
  render(<UserKeyPage />)
  expect(screen.getByText(connection.baseUrl)).toBeTruthy()
  expect(screen.getByText(connection.accountId)).toBeTruthy()
  expect(document.querySelectorAll('input')).toHaveLength(1)
  expect(
    screen.getByLabelText('profiles.fields.apiKey').getAttribute('type'),
  ).toBe('password')
})
it('saves within the existing profile, keeping the previous User, and waits for durable storage before navigation', async () => {
  let resolve!: () => void
  mocks.flush.mockReturnValue(
    new Promise<void>((done) => {
      resolve = done
    }),
  )
  render(<UserKeyPage />)
  fireEvent.change(screen.getByLabelText('profiles.fields.apiKey'), {
    target: { value: 'bob-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'profiles.saveActivate' }))
  await waitFor(() => expect(mocks.flush).toHaveBeenCalledOnce())
  expect(mocks.switch).toHaveBeenCalledWith(
    expect.objectContaining({
      accountId: 'team',
      userId: 'bob',
      apiKey: 'bob-key',
      preserveProfile: true,
      signal: expect.any(AbortSignal),
    }),
  )
  expect(mocks.navigate).not.toHaveBeenCalled()
  resolve()
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/settings' }),
  )
})
it('cancels pending verification when leaving the page', async () => {
  let resolve!: (value: unknown) => void
  mocks.verify.mockReturnValue(
    new Promise((done) => {
      resolve = done
    }),
  )
  const view = render(<UserKeyPage />)
  fireEvent.change(screen.getByLabelText('profiles.fields.apiKey'), {
    target: { value: 'bob-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'profiles.saveActivate' }))
  const signal = mocks.verify.mock.calls[0][2] as AbortSignal
  view.unmount()
  expect(signal.aborted).toBe(true)
  resolve({ ...connection, apiKey: 'bob-key', userId: 'bob' })
  await Promise.resolve()
  expect(mocks.switch).not.toHaveBeenCalled()
})

it('continues into the workbench after contextual key setup and durable storage', async () => {
  mocks.search = { returnTo: '/home' }
  let resolve!: () => void
  mocks.flush.mockReturnValue(
    new Promise<void>((done) => {
      resolve = done
    }),
  )
  render(<UserKeyPage />)
  fireEvent.change(screen.getByLabelText('profiles.fields.apiKey'), {
    target: { value: 'bob-key' },
  })
  fireEvent.click(screen.getByRole('button', { name: 'profiles.saveActivate' }))
  await waitFor(() => expect(mocks.flush).toHaveBeenCalledOnce())
  expect(mocks.navigate).not.toHaveBeenCalled()
  resolve()
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/home' }),
  )
})
