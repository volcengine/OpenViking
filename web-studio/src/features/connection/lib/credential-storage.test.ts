import {
  CREDENTIAL_SLOTS,
  CredentialStorage,
} from '#/features/connection/lib/credential-storage'
import { describe, expect, it, vi } from 'vitest'

const [connectionSlot, profilesSlot] = CREDENTIAL_SLOTS
function memoryStorage() {
  const data = new Map<string, string>()
  return {
    data,
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => {
      data.set(key, value)
    },
  }
}
async function fixture() {
  const key = await crypto.subtle.generateKey(
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
  const disk = memoryStorage()
  const getKey = vi.fn(async (_create: boolean) => key)
  return { key, disk, getKey, open: () => new CredentialStorage(disk, getKey) }
}

describe('automatic credential encryption', () => {
  it('migrates Root and multiple User keys, then restores them after a fresh startup without a password', async () => {
    const f = await fixture()
    const profiles = JSON.stringify({
      profiles: [
        {
          connection: { adminApiKey: 'root-secret', apiKey: 'alice-secret' },
          identities: [{ apiKey: 'bob-secret' }],
        },
      ],
    })
    f.disk.setItem(profilesSlot, profiles)
    f.disk.setItem(connectionSlot, 'alice-secret')
    const first = f.open()
    await first.initialize()
    for (const value of f.disk.data.values()) {
      expect(value).not.toContain('secret')
      expect(JSON.parse(value).format).toBe('ov-credentials-aes-gcm-v1')
    }
    const second = f.open()
    await second.initialize()
    expect(second.read(profilesSlot)).toBe(profiles)
    expect(second.read(connectionSlot)).toBe('alice-secret')
    expect(f.getKey.mock.calls[1][0]).toBe(false)
    expect(f.key.extractable).toBe(false)
    await expect(crypto.subtle.exportKey('raw', f.key)).rejects.toThrow()
  })
  it('uses different IVs and persists the newest rapid identity switch', async () => {
    const f = await fixture()
    const store = f.open()
    await store.initialize()
    store.write(connectionSlot, 'same')
    await store.flush()
    const first = f.disk.getItem(connectionSlot)
    store.write(connectionSlot, 'same')
    await store.flush()
    expect(f.disk.getItem(connectionSlot)).not.toBe(first)
    store.write(connectionSlot, 'alice')
    store.write(connectionSlot, 'bob')
    await store.flush()
    const reloaded = f.open()
    await reloaded.initialize()
    expect(reloaded.read(connectionSlot)).toBe('bob')
  })
  it('rejects tampering or swapping records between slots without clearing existing ciphertext', async () => {
    const f = await fixture()
    const store = f.open()
    await store.initialize()
    store.write(connectionSlot, 'secret')
    await store.flush()
    const original = f.disk.getItem(connectionSlot)!
    f.disk.setItem(profilesSlot, original)
    await expect(f.open().initialize()).rejects.toThrow()
    expect(f.disk.getItem(connectionSlot)).toBe(original)
  })
  it('keeps ciphertext when the encryption key is lost and never generates a replacement', async () => {
    const f = await fixture()
    const store = f.open()
    await store.initialize()
    store.write(connectionSlot, 'secret')
    await store.flush()
    const before = f.disk.getItem(connectionSlot)
    const getKey = vi.fn(async (_create: boolean): Promise<CryptoKey> => {
      throw new Error('lost')
    })
    await expect(
      new CredentialStorage(f.disk, getKey).initialize(),
    ).rejects.toThrow('lost')
    expect(getKey).toHaveBeenCalledWith(false)
    expect(f.disk.getItem(connectionSlot)).toBe(before)
  })
  it('reports storage failure, does not write plaintext, and retries without hiding a failed slot', async () => {
    const f = await fixture()
    const onError = vi.fn()
    let fail = true
    const disk = {
      ...f.disk,
      setItem: (slot: string, value: string) => {
        if (slot === profilesSlot && fail) throw new Error('quota')
        f.disk.setItem(slot, value)
      },
    }
    const store = new CredentialStorage(disk, f.getKey, onError)
    await store.initialize()
    store.write(profilesSlot, 'user-secret')
    store.write(connectionSlot, 'root-secret')
    await expect(store.flush()).rejects.toThrow('quota')
    expect(onError).toHaveBeenCalledOnce()
    expect(f.disk.getItem(profilesSlot)).toBeNull()
    expect(f.disk.getItem(connectionSlot)).not.toContain('root-secret')
    fail = false
    store.write(profilesSlot, 'user-secret')
    await store.flush()
    const reloaded = f.open()
    await reloaded.initialize()
    expect(reloaded.read(profilesSlot)).toBe('user-secret')
  })
  it('does not let a stale tab overwrite credentials saved by another tab', async () => {
    const f = await fixture()
    const first = f.open()
    const second = f.open()
    await first.initialize()
    await second.initialize()
    first.write(profilesSlot, 'new-user-key')
    await first.flush()
    second.write(profilesSlot, 'stale-user-key')
    await expect(second.flush()).rejects.toThrow('another tab')
    const reloaded = f.open()
    await reloaded.initialize()
    expect(reloaded.read(profilesSlot)).toBe('new-user-key')
  })
})
