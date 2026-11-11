type RandomUuid = ReturnType<Crypto['randomUUID']>

function randomByte(): number {
  return Math.floor(Math.random() * 256)
}

function createFallbackRandomUuid(): RandomUuid {
  const browserCrypto =
    typeof globalThis.crypto !== 'undefined' ? globalThis.crypto : undefined

  const bytes = new Uint8Array(16)
  if (typeof browserCrypto?.getRandomValues === 'function') {
    browserCrypto.getRandomValues(bytes)
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = randomByte()
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80

  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0'))

  return `${hex.slice(0, 4).join('')}-${hex.slice(4, 6).join('')}-${hex
    .slice(6, 8)
    .join('')}-${hex.slice(8, 10).join('')}-${hex.slice(10, 16).join('')}`
}

export function createRandomUuid(): RandomUuid {
  const browserCrypto =
    typeof globalThis.crypto !== 'undefined' ? globalThis.crypto : undefined

  if (typeof browserCrypto?.randomUUID === 'function') {
    return browserCrypto.randomUUID()
  }

  return createFallbackRandomUuid()
}

export function createBrowserId(prefix: string): string {
  return `${prefix}_${createRandomUuid().replace(/-/g, '')}`
}
