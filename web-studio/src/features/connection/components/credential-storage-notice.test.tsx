// @vitest-environment jsdom
import {
  CredentialStorageNotice,
  registerCredentialStorageNotifications,
} from '#/features/connection/components/credential-storage-notice'
import { cleanup, render } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'

const mocks = vi.hoisted(() => ({
  mode: 'persistent',
  warning: vi.fn(),
  error: vi.fn(),
  dismiss: vi.fn(),
}))
vi.mock('#/features/connection/lib/credential-storage', () => ({
  credentialStorageMode: () => mocks.mode,
}))
vi.mock('sonner', () => ({
  toast: { warning: mocks.warning, error: mocks.error, dismiss: mocks.dismiss },
}))
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}))
afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

it('warns when connections cannot survive a page reload', () => {
  mocks.mode = 'session'
  render(<CredentialStorageNotice />)
  expect(mocks.warning).toHaveBeenCalledWith('credentialStorage.sessionOnly', {
    id: 'credential-storage-mode',
    duration: Infinity,
  })
})

it.each(['persistent', 'plaintext'])(
  'does not warn when connections can be restored in %s mode',
  (mode) => {
    mocks.mode = mode
    render(<CredentialStorageNotice />)
    expect(mocks.warning).not.toHaveBeenCalled()
  },
)

it.each(['conflict', 'unavailable'])(
  'reports %s storage failures accurately and dismisses the notice after recovery',
  (reason) => {
    const release = registerCredentialStorageNotifications((key) => key)
    try {
      window.dispatchEvent(
        new CustomEvent('ov-credential-storage-error', { detail: reason }),
      )
      expect(mocks.error).toHaveBeenCalledWith(
        reason === 'conflict'
          ? 'credentialStorage.conflictError'
          : 'credentialStorage.saveError',
        { id: 'credential-storage', duration: Infinity },
      )
      window.dispatchEvent(new Event('ov-credential-storage-saved'))
      expect(mocks.dismiss).toHaveBeenCalledWith('credential-storage')
    } finally {
      release()
    }
  },
)
