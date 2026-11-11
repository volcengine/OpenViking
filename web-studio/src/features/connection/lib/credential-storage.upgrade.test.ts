// @vitest-environment jsdom
import { webcrypto } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const connection = {
  serviceSelection: 'opensource' as const,
  baseUrl: 'http://legacy-service.test:1933',
  apiKey: 'synthetic-user-key',
  adminApiKey: 'synthetic-admin-key',
  accountId: 'team',
  userId: 'alice',
}
const profiles = {
  activeId: 'saved',
  profiles: [{ id: 'saved', name: 'Saved workspace', connection }],
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
  vi.resetModules()
})
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  localStorage.clear()
  sessionStorage.clear()
  vi.resetModules()
})

async function start() {
  const storage = await import('./credential-storage')
  await storage.initializeCredentialStorage()
  const onboarding = await import('./onboarding')
  return { ...storage, ...onboarding }
}

describe.each(['insecure HTTP', 'IndexedDB unavailable', 'IndexedDB denied'])(
  'upgrade without encrypted persistence: %s',
  (environment) => {
    beforeEach(() => {
      vi.stubGlobal(
        'crypto',
        environment === 'insecure HTTP'
          ? { getRandomValues: webcrypto.getRandomValues.bind(webcrypto) }
          : webcrypto,
      )
      vi.stubGlobal(
        'indexedDB',
        environment === 'IndexedDB denied'
          ? {
              open: () => {
                throw new Error('access denied')
              },
            }
          : undefined,
      )
    })

    it.each(
      ['legacy connection', 'saved profiles'].flatMap((source) =>
        [null, 'pending', 'complete'].map((setupMarker) => [
          source,
          setupMarker,
        ]),
      ),
    )(
      'reuses pre-6812 %s with setup=%s and no workspace session marker',
      async (source, setupMarker) => {
        if (setupMarker) localStorage.setItem('ov_studio_setup', setupMarker)
        const slot =
          source === 'legacy connection'
            ? 'ov_console_connection'
            : 'ov_console_connection_profiles'
        const raw = JSON.stringify(
          source === 'legacy connection' ? connection : profiles,
        )
        localStorage.setItem(slot, raw)
        const api = await start()
        expect(api.needsStudioSetup()).toBe(false)
        expect(api.savedSetupConnections()[0].connection).toMatchObject(
          connection,
        )
        expect(localStorage.getItem(slot)).toBe(raw)
        if (source === 'legacy connection') {
          const { readStoredConnection } = await import('./connection-runtime')
          expect(readStoredConnection()).toMatchObject(connection)
        } else {
          const { readConnectionProfiles } = await import('./profiles')
          expect(readConnectionProfiles(connection)).toEqual(profiles)
        }
        sessionStorage.clear()
        vi.resetModules()
        const reloaded = await start()
        expect(reloaded.needsStudioSetup()).toBe(false)
      },
    )

    it('persists a newly configured workspace and identity changes across reloads', async () => {
      const api = await start()
      expect(api.needsStudioSetup()).toBe(true)
      await api.completeStudioSetup(connection, 'New workspace')
      const saved = api.savedSetupConnections()[0]
      await api.completeStudioSetup(
        { ...connection, userId: 'bob', apiKey: 'synthetic-bob-key' },
        saved.name,
        saved.id,
      )
      sessionStorage.clear()
      vi.resetModules()
      const reloaded = await start()
      expect(reloaded.credentialStorageMode()).toBe('plaintext')
      expect(reloaded.needsStudioSetup()).toBe(false)
      expect(reloaded.savedSetupConnections()).toHaveLength(1)
      expect(reloaded.savedSetupConnections()[0]).toMatchObject({
        id: saved.id,
        connection: { userId: 'bob', apiKey: 'synthetic-bob-key' },
      })
    })

    it('restores a completed same-origin development connection without a key', async () => {
      localStorage.setItem('ov_studio_setup', 'complete')
      localStorage.setItem(
        'ov_console_connection',
        JSON.stringify({
          ...connection,
          baseUrl: window.location.origin,
          apiKey: '',
          adminApiKey: '',
        }),
      )
      const api = await start()
      expect(api.needsStudioSetup()).toBe(false)
    })
  },
)

it('migrates fallback credentials into encrypted persistence when encryption becomes available', async () => {
  vi.stubGlobal('crypto', {})
  const api = await start()
  await api.completeStudioSetup(connection, 'Migrated workspace')
  const expected = new Map(
    api.CREDENTIAL_SLOTS.map((slot) => [slot, localStorage.getItem(slot)]),
  )
  vi.stubGlobal('crypto', webcrypto)
  const key = await crypto.subtle.generateKey(
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
  const getKey = async () => key
  const migrated = new api.CredentialStorage(localStorage, getKey)
  await migrated.initialize()
  for (const [slot, raw] of expected) {
    expect(migrated.read(slot)).toBe(raw)
    expect(JSON.parse(localStorage.getItem(slot)!).format).toBe(
      'ov-credentials-aes-gcm-v1',
    )
    expect(localStorage.getItem(slot)).not.toContain('synthetic-')
  }
  const restored = new api.CredentialStorage(localStorage, getKey)
  await restored.initialize()
  for (const [slot, raw] of expected) expect(restored.read(slot)).toBe(raw)
})

it('uses page memory when local storage cannot be read', async () => {
  vi.stubGlobal('crypto', {})
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
    throw new Error('access denied')
  })
  const api = await import('./credential-storage')
  await api.initializeCredentialStorage()
  expect(api.credentialStorageMode()).toBe('session')
  api.writeCredentialStorage(
    'ov_console_connection',
    JSON.stringify(connection),
  )
  await api.flushCredentialStorage()
  expect(api.readCredentialStorage('ov_console_connection')).toBe(
    JSON.stringify(connection),
  )
})

it('reports a failed plaintext save instead of pretending it was retained', async () => {
  vi.stubGlobal('crypto', {})
  const api = await start()
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota')
  })
  await expect(api.completeStudioSetup(connection, 'Unsaved')).rejects.toThrow(
    'quota',
  )
  expect(sessionStorage.getItem('ov_studio_workspace_selected')).toBeNull()
})

it('keeps helper save failures visible to flush until each failed slot is saved', async () => {
  vi.stubGlobal('crypto', {})
  const api = await start()
  const { persistConnection } = await import('./connection-runtime')
  const { persistConnectionProfiles } = await import('./profiles')
  const fail = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
    throw new Error('quota')
  })
  persistConnection(connection)
  persistConnectionProfiles(profiles)
  await expect(api.flushCredentialStorage()).rejects.toThrow('quota')
  fail.mockRestore()
  persistConnection(connection)
  await expect(api.flushCredentialStorage()).rejects.toThrow('quota')
  persistConnectionProfiles(profiles)
  await expect(api.flushCredentialStorage()).resolves.toBeUndefined()
})

it('does not overwrite a record updated by another tab', async () => {
  vi.stubGlobal('crypto', {})
  localStorage.setItem('ov_console_connection', JSON.stringify(connection))
  const api = await start()
  const newer = JSON.stringify({ ...connection, apiKey: 'newer-synthetic-key' })
  localStorage.setItem('ov_console_connection', newer)
  expect(() =>
    api.writeCredentialStorage('ov_console_connection', 'stale'),
  ).toThrow('another tab')
  expect(localStorage.getItem('ov_console_connection')).toBe(newer)
  await expect(api.flushCredentialStorage()).rejects.toThrow('another tab')
})

it('restores readable legacy data without overwriting an encrypted slot', async () => {
  vi.stubGlobal('crypto', {})
  const encrypted =
    '{"format":"ov-credentials-aes-gcm-v1","ciphertext":"preserve-me"}'
  localStorage.setItem('ov_console_connection', JSON.stringify(connection))
  localStorage.setItem('ov_console_connection_profiles', encrypted)
  const api = await start()
  expect(api.credentialStorageMode()).toBe('session')
  expect(api.needsStudioSetup()).toBe(false)
  api.writeCredentialStorage('ov_console_connection', 'temporary')
  api.writeCredentialStorage('ov_console_connection_profiles', 'temporary')
  await api.flushCredentialStorage()
  expect(localStorage.getItem('ov_console_connection')).toBe(
    JSON.stringify(connection),
  )
  expect(localStorage.getItem('ov_console_connection_profiles')).toBe(encrypted)
})
