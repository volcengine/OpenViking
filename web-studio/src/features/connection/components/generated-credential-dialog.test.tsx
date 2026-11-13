// @vitest-environment jsdom
import { GeneratedCredentialDialog } from '#/features/connection/components/generated-credential-dialog'
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  change: vi.fn(),
  clear: vi.fn(),
  navigate: vi.fn(),
  flush: vi.fn(),
}))
vi.mock('#/features/connection/hooks/use-app-connection', () => ({
  useAppConnection: () => ({
    clearGeneratedCredential: mocks.clear,
    connection: { accountId: 'team', userId: 'manager', apiKey: 'manager-key' },
    generatedCredential: {
      accountId: 'team',
      userId: 'alice',
      apiKey: 'alice-key',
    },
    switchIdentity: mocks.change,
  }),
}))
vi.mock('#/features/connection/lib/credential-storage', () => ({
  flushCredentialStorage: mocks.flush,
}))
vi.mock('@tanstack/react-router', () => ({ useNavigate: () => mocks.navigate }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})
it('enters the workbench only after an explicit action, verified identity switch and encrypted persistence', async () => {
  let finish!: () => void
  mocks.flush.mockReturnValue(
    new Promise<void>((resolve) => {
      finish = resolve
    }),
  )
  render(<GeneratedCredentialDialog />)
  expect(mocks.change).not.toHaveBeenCalled()
  fireEvent.click(
    screen.getByRole('button', { name: 'appShell:modes.enterWorkbench' }),
  )
  await waitFor(() => expect(mocks.flush).toHaveBeenCalled())
  expect(mocks.change).toHaveBeenCalledWith({
    accountId: 'team',
    userId: 'alice',
    apiKey: 'alice-key',
    allowLegacyIdentityFallback: true,
    preserveProfile: true,
  })
  expect(mocks.navigate).not.toHaveBeenCalled()
  finish()
  await waitFor(() =>
    expect(mocks.navigate).toHaveBeenCalledWith({ to: '/home' }),
  )
  expect(mocks.clear).toHaveBeenCalled()
})
it('stays on the credential result when persistence fails', async () => {
  mocks.flush.mockRejectedValue(new Error('write-failed'))
  render(<GeneratedCredentialDialog />)
  fireEvent.click(
    screen.getByRole('button', { name: 'appShell:modes.enterWorkbench' }),
  )
  await waitFor(() => expect(mocks.flush).toHaveBeenCalled())
  expect(mocks.navigate).not.toHaveBeenCalled()
  expect(mocks.clear).not.toHaveBeenCalled()
})
