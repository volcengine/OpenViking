// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'

afterEach(() => {
  vi.unstubAllGlobals()
  localStorage.clear()
})
it('supports an insecure origin in page memory without reading, replacing or writing persistent credential records', async () => {
  vi.stubGlobal('crypto', {
    getRandomValues: crypto.getRandomValues.bind(crypto),
  })
  vi.resetModules()
  const previous =
    '{"format":"ov-credentials-aes-gcm-v1","ciphertext":"preserve-me"}'
  localStorage.setItem('ov_console_connection_profiles', previous)
  const api = await import('#/features/connection/lib/credential-storage')
  await api.initializeCredentialStorage()
  expect(api.credentialStorageMode()).toBe('session')
  expect(api.readCredentialStorage('ov_console_connection_profiles')).toBeNull()
  api.writeCredentialStorage(
    'ov_console_connection_profiles',
    'synthetic-new-user-key',
  )
  await api.flushCredentialStorage()
  expect(api.readCredentialStorage('ov_console_connection_profiles')).toBe(
    'synthetic-new-user-key',
  )
  expect(localStorage.getItem('ov_console_connection_profiles')).toBe(previous)
  vi.resetModules()
  const reloaded = await import('#/features/connection/lib/credential-storage')
  await reloaded.initializeCredentialStorage()
  expect(
    reloaded.readCredentialStorage('ov_console_connection_profiles'),
  ).toBeNull()
  expect(localStorage.getItem('ov_console_connection_profiles')).toBe(previous)
})

it('falls back to memory when IndexedDB is exposed but opening it is denied', async () => {
  const { webcrypto } = await import('node:crypto')
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('indexedDB', {
    open: () => {
      throw new Error('access denied')
    },
  })
  vi.resetModules()
  const previous =
    '{"format":"ov-credentials-aes-gcm-v1","ciphertext":"preserve-me"}'
  localStorage.setItem('ov_console_connection_profiles', previous)
  const api = await import('#/features/connection/lib/credential-storage')
  await api.initializeCredentialStorage()
  expect(api.credentialStorageMode()).toBe('session')
  api.writeCredentialStorage('ov_console_connection_profiles', 'temporary-key')
  await api.flushCredentialStorage()
  expect(api.readCredentialStorage('ov_console_connection_profiles')).toBe(
    'temporary-key',
  )
  expect(localStorage.getItem('ov_console_connection_profiles')).toBe(previous)
})

it('does not fall back when an existing encrypted record has lost its key', async () => {
  const { webcrypto } = await import('node:crypto')
  vi.stubGlobal('crypto', webcrypto)
  vi.stubGlobal('indexedDB', {
    open: () => {
      const request: any = {}
      queueMicrotask(() => {
        const transaction: any = {
          objectStore: () => ({
            get: () => {
              const getRequest: any = {}
              queueMicrotask(() => getRequest.onsuccess())
              return getRequest
            },
          }),
          abort: () => queueMicrotask(() => transaction.onabort()),
        }
        request.result = { transaction: () => transaction, close: () => {} }
        request.onsuccess()
      })
      return request
    },
  })
  vi.resetModules()
  const previous =
    '{"format":"ov-credentials-aes-gcm-v1","ciphertext":"preserve-me"}'
  localStorage.setItem('ov_console_connection_profiles', previous)
  const api = await import('#/features/connection/lib/credential-storage')
  await expect(api.initializeCredentialStorage()).rejects.toThrow(
    'Credential key missing',
  )
  expect(localStorage.getItem('ov_console_connection_profiles')).toBe(previous)
})
