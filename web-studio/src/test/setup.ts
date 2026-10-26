/// <reference types="vitest/jsdom" />

if (typeof jsdom !== 'undefined') {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    enumerable: true,
    value: jsdom.window.localStorage,
    writable: true,
  })
}
