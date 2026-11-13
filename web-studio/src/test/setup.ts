/// <reference types="vitest/jsdom" />

import { TextEncoder as NodeTextEncoder } from 'node:util'

if (typeof jsdom !== 'undefined') {
  // Node's encoder returns bytes from a different realm than the file viewer.
  globalThis.TextEncoder = class extends NodeTextEncoder {
    override encode(input?: string): Uint8Array<ArrayBuffer> {
      return Uint8Array.from(super.encode(input))
    }
  }
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    enumerable: true,
    value: jsdom.window.localStorage,
    writable: true,
  })
}
