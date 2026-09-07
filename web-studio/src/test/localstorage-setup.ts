// Vitest 3.2.4's jsdom environment does not install a `localStorage` over
// Node >= 22.4's native experimental localStorage getter, so `localStorage`
// resolves to undefined inside jsdom tests — and the source-level
// `typeof window === 'undefined'` guard does not fire because `window`
// exists there. Give tests a working Storage unless one is already present;
// once vitest/jsdom ships its own, this becomes a no-op.
const existing = (globalThis as Record<string, unknown>).localStorage

if (existing == null) {
  const store = new Map<string, string>()

  const storage: Storage = {
    get length() {
      return store.size
    },
    clear() {
      store.clear()
    },
    getItem(key) {
      return store.has(key) ? store.get(key)! : null
    },
    key(index) {
      return Array.from(store.keys())[index] ?? null
    },
    removeItem(key) {
      store.delete(key)
    },
    setItem(key, value) {
      store.set(String(key), String(value))
    },
  }

  Object.defineProperty(globalThis, 'localStorage', {
    value: storage,
    configurable: true,
    writable: true,
  })
}
