/* eslint-disable studio-i18n/no-literal-message -- Internal failures are mapped to localized startup/save messages at the UI boundary. */
/**
 * Automatic encryption at rest, not an XSS/extension security boundary.
 * The non-exportable key lives in IndexedDB; plaintext only lives in memory.
 * Same-origin code can still ask Web Crypto to decrypt with that key.
 */
export const CREDENTIAL_SLOTS = [
  'ov_console_connection',
  'ov_console_connection_profiles',
] as const
type Slot = (typeof CREDENTIAL_SLOTS)[number]
const FORMAT = 'ov-credentials-aes-gcm-v1'
const DATABASE = 'ov-studio-credentials'
const KEY_STORE = 'keys'

class CredentialStorageUnavailable extends Error {}
class CredentialKeyMissing extends Error {}

function encode(bytes: Uint8Array): string {
  return btoa(Array.from(bytes, (byte) => String.fromCharCode(byte)).join(''))
}
function decode(value: string): Uint8Array<ArrayBuffer> {
  return Uint8Array.from(atob(value), (character) => character.charCodeAt(0))
}
function isEncrypted(raw: string): boolean {
  try {
    return JSON.parse(raw)?.format === FORMAT
  } catch {
    return false
  }
}

/** An atomic read/create transaction makes concurrent first tabs share one key. */
async function browserKey(create: boolean): Promise<CryptoKey> {
  try {
    return await loadBrowserKey(create)
  } catch (error) {
    if (error instanceof CredentialKeyMissing) throw error
    throw new CredentialStorageUnavailable(
      'Credential key storage unavailable',
      { cause: error },
    )
  }
}

async function loadBrowserKey(create: boolean): Promise<CryptoKey> {
  if (
    typeof crypto === 'undefined' ||
    typeof crypto.subtle === 'undefined' ||
    typeof indexedDB === 'undefined'
  )
    throw new Error('Encrypted credential storage unavailable')
  const candidate = create
    ? await crypto.subtle.generateKey({ name: 'AES-GCM', length: 256 }, false, [
        'encrypt',
        'decrypt',
      ])
    : undefined
  const db = await new Promise<IDBDatabase>((resolve, reject) => {
    const request = indexedDB.open(DATABASE, 1)
    request.onupgradeneeded = () => request.result.createObjectStore(KEY_STORE)
    request.onsuccess = () => resolve(request.result)
    request.onerror = () =>
      reject(new Error('Credential key storage unavailable'))
    request.onblocked = () =>
      reject(new Error('Credential key storage blocked'))
  })
  try {
    return await new Promise<CryptoKey>((resolve, reject) => {
      const transaction = db.transaction(KEY_STORE, 'readwrite')
      const store = transaction.objectStore(KEY_STORE)
      let key: CryptoKey | undefined
      let missing = false
      const request = store.get('encryption-key')
      request.onsuccess = () => {
        key = request.result ?? candidate
        if (!key) {
          missing = true
          transaction.abort()
          return
        }
        if (!request.result) store.add(key, 'encryption-key')
      }
      transaction.oncomplete = () => resolve(key!)
      transaction.onabort = transaction.onerror = () =>
        reject(
          missing
            ? new CredentialKeyMissing('Credential key missing')
            : new Error('Credential key unavailable'),
        )
    })
  } finally {
    db.close()
  }
}

export class CredentialStorage {
  private key?: CryptoKey
  private values = new Map<Slot, string>()
  private persisted = new Map<Slot, string | null>()
  private pending: Promise<void> = Promise.resolve()
  private initialization?: Promise<void>
  private failures = new Map<Slot, unknown>()

  constructor(
    private readonly storage: Pick<Storage, 'getItem' | 'setItem'>,
    private readonly getKey = browserKey,
    private readonly onError: () => void = () => {},
  ) {}

  initialize(): Promise<void> {
    this.initialization ??= this.load()
    return this.initialization
  }

  private async load() {
    const entries = CREDENTIAL_SLOTS.map(
      (slot) => [slot, this.storage.getItem(slot)] as const,
    )
    for (const [slot, raw] of entries) this.persisted.set(slot, raw)
    // Never replace a lost key when ciphertext exists: preserve data for recovery.
    this.key = await this.getKey(
      !entries.some(([, raw]) => raw && isEncrypted(raw)),
    )
    const decoded = await Promise.all(
      entries.map(async ([slot, raw]) => {
        if (!raw) return [slot, null] as const
        if (!isEncrypted(raw)) return [slot, raw] as const
        const envelope = JSON.parse(raw)
        const bytes = await crypto.subtle.decrypt(
          {
            name: 'AES-GCM',
            iv: decode(envelope.iv),
            additionalData: new TextEncoder().encode(slot),
          },
          this.key!,
          decode(envelope.ciphertext),
        )
        return [slot, new TextDecoder().decode(bytes)] as const
      }),
    )
    // Decrypt everything before migrating anything. Corruption must not erase profiles.
    for (const [slot, raw] of decoded)
      if (raw !== null) this.values.set(slot, raw)
    for (const [slot, raw] of entries)
      if (raw && !isEncrypted(raw)) await this.encryptAndStore(slot, raw)
  }

  read(slot: Slot): string | null {
    if (!this.key) throw new Error('Credential storage not initialized')
    return this.values.get(slot) ?? null
  }

  write(slot: Slot, value: string): void {
    if (!this.key) throw new Error('Credential storage not initialized')
    this.values.set(slot, value)
    // Serialize writes, including rapid User switches, so old ciphertext cannot win.
    this.pending = this.pending
      .catch(() => {})
      .then(async () => {
        await this.encryptAndStore(slot, value)
        this.failures.delete(slot)
      })
    void this.pending.catch((error: unknown) => {
      this.failures.set(slot, error)
      this.onError()
    })
  }

  async flush(): Promise<void> {
    let pending
    do {
      pending = this.pending
      await pending
    } while (pending !== this.pending)
    if (this.failures.size) throw this.failures.values().next().value
  }

  private async encryptAndStore(slot: Slot, value: string) {
    const iv = crypto.getRandomValues(new Uint8Array(12))
    const ciphertext = await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv, additionalData: new TextEncoder().encode(slot) },
      this.key!,
      new TextEncoder().encode(value),
    )
    // Another tab must not silently replace newly saved User credentials with
    // its older in-memory profile list. Keep the newer record intact.
    if (this.storage.getItem(slot) !== (this.persisted.get(slot) ?? null))
      throw new Error('Credentials changed in another tab')
    const encrypted = JSON.stringify({
      format: FORMAT,
      iv: encode(iv),
      ciphertext: encode(new Uint8Array(ciphertext)),
    })
    this.storage.setItem(slot, encrypted)
    this.persisted.set(slot, encrypted)
  }
}

class SessionCredentialStorage {
  constructor(protected readonly values = new Map<Slot, string>()) {}
  async initialize() {}
  read(slot: Slot): string | null {
    return this.values.get(slot) ?? null
  }
  write(slot: Slot, value: string) {
    this.values.set(slot, value)
  }
  async flush() {}
}

/** Preserve pre-encryption persistence on HTTP origins, with an explicit notice. */
class PlaintextCredentialStorage extends SessionCredentialStorage {
  private failures = new Map<Slot, unknown>()

  constructor(
    private readonly disk: Storage,
    private readonly persisted: Map<Slot, string | null>,
    values: Map<Slot, string>,
  ) {
    super(values)
  }

  override write(slot: Slot, value: string) {
    try {
      // A tab using encrypted storage or newer credentials must win.
      if (this.disk.getItem(slot) !== this.persisted.get(slot))
        throw new Error('Credentials changed in another tab')
      this.disk.setItem(slot, value)
      this.persisted.set(slot, value)
      super.write(slot, value)
      this.failures.delete(slot)
    } catch (error) {
      this.failures.set(slot, error)
      window.dispatchEvent(new Event('ov-credential-storage-error'))
      throw error
    }
  }

  override async flush() {
    if (this.failures.size) throw this.failures.values().next().value
  }
}

function unencryptedStorage(): SessionCredentialStorage {
  try {
    const disk = window.localStorage
    const entries = CREDENTIAL_SLOTS.map(
      (slot) => [slot, disk.getItem(slot)] as const,
    )
    const values = new Map<Slot, string>()
    for (const [slot, raw] of entries)
      if (raw && !isEncrypted(raw)) values.set(slot, raw)
    // Without the key, never overwrite ciphertext in either slot. Readable
    // legacy records can still restore the current workspace in page memory.
    if (entries.some(([, raw]) => raw && isEncrypted(raw)))
      return new SessionCredentialStorage(values)
    return new PlaintextCredentialStorage(disk, new Map(entries), values)
  } catch {
    return new SessionCredentialStorage()
  }
}

let storage: CredentialStorage | SessionCredentialStorage | undefined
export function credentialStorageMode():
  | 'persistent'
  | 'plaintext'
  | 'session' {
  if (storage instanceof PlaintextCredentialStorage) return 'plaintext'
  return storage instanceof SessionCredentialStorage ? 'session' : 'persistent'
}
export async function initializeCredentialStorage(): Promise<void> {
  storage ??=
    typeof crypto === 'undefined' ||
    typeof crypto.subtle === 'undefined' ||
    typeof indexedDB === 'undefined'
      ? unencryptedStorage()
      : new CredentialStorage(window.localStorage, browserKey, () => {
          window.dispatchEvent(new Event('ov-credential-storage-error'))
        })
  try {
    await storage.initialize()
  } catch (error) {
    if (!(error instanceof CredentialStorageUnavailable)) throw error
    storage = unencryptedStorage()
    await storage.initialize()
  }
  // The runtime SDK does not persist its key; remove the obsolete plaintext mirror.
  window.sessionStorage.removeItem('ov_console_api_key')
}
export function readCredentialStorage(slot: Slot): string | null {
  return storage!.read(slot)
}
export function writeCredentialStorage(slot: Slot, value: string): void {
  storage!.write(slot, value)
}
export function flushCredentialStorage(): Promise<void> {
  return storage!.flush()
}
