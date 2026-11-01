// @vitest-environment jsdom
import { CredentialStorageNotice } from '#/features/connection/components/credential-storage-notice'
import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  mode: 'persistent',
  warning: vi.fn(),
}))
vi.mock('#/features/connection/lib/credential-storage', () => ({
  credentialStorageMode: () => mocks.mode,
}))
vi.mock('sonner', () => ({ toast: { warning: mocks.warning } }))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it.each([
  ['plaintext', 'credentialStorage.plaintext'],
  ['session', 'credentialStorage.sessionOnly'],
])('discloses the actual storage limitation in %s mode', (mode, message) => {
  mocks.mode = mode
  render(<CredentialStorageNotice />)
  expect(mocks.warning).toHaveBeenCalledWith(message, {
    id: 'credential-storage-mode',
    duration: Infinity,
  })
})

it('does not warn when credentials are encrypted', () => {
  mocks.mode = 'persistent'
  render(<CredentialStorageNotice />)
  expect(mocks.warning).not.toHaveBeenCalled()
})
