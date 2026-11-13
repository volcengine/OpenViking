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
  if (!Reflect.has(globalThis, 'ResizeObserver')) {
    globalThis.ResizeObserver = class implements ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    }
  }
  // jsdom hides popovers by default and has no API to show them in the top layer.
  if (!Reflect.has(HTMLElement.prototype, 'showPopover')) {
    HTMLElement.prototype.showPopover = function () {
      this.style.display = 'block'
    }
  }
}
