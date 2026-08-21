import { useCallback, useSyncExternalStore } from 'react'

const STORAGE_KEY = 'ov-session-titles'
const snapshots = new Map<string, Record<string, string>>()

export function createSessionTitleStorageKey(identityScopeKey: string): string {
  return `${STORAGE_KEY}.${encodeURIComponent(identityScopeKey)}`
}

function readTitles(storageKey: string): Record<string, string> {
  try {
    const raw = localStorage.getItem(storageKey)
    return raw ? (JSON.parse(raw) as Record<string, string>) : {}
  } catch {
    return {}
  }
}

function getTitlesSnapshot(storageKey: string): Record<string, string> {
  const current = snapshots.get(storageKey)
  if (current) {
    return current
  }
  const initial = readTitles(storageKey)
  snapshots.set(storageKey, initial)
  return initial
}

function writeTitles(storageKey: string, titles: Record<string, string>) {
  const serialized = JSON.stringify(titles)
  try {
    localStorage.setItem(storageKey, serialized)
  } catch {
    // Keep the in-memory snapshot usable in restricted browser environments.
  }
  snapshots.set(storageKey, titles)
  window.dispatchEvent(
    new StorageEvent('storage', { key: storageKey, newValue: serialized }),
  )
}

function subscribe(storageKey: string, onStoreChange: () => void) {
  const handler = (event: StorageEvent) => {
    if (event.key === storageKey || event.key === null) {
      if (event.key === storageKey && event.newValue !== null) {
        try {
          snapshots.set(
            storageKey,
            JSON.parse(event.newValue) as Record<string, string>,
          )
        } catch {
          snapshots.set(storageKey, readTitles(storageKey))
        }
      } else {
        snapshots.set(storageKey, readTitles(storageKey))
      }
      onStoreChange()
    }
  }
  window.addEventListener('storage', handler)
  return () => window.removeEventListener('storage', handler)
}

export function setSessionTitle(
  identityScopeKey: string,
  sessionId: string,
  title: string,
) {
  const storageKey = createSessionTitleStorageKey(identityScopeKey)
  const titles = { ...getTitlesSnapshot(storageKey), [sessionId]: title }
  writeTitles(storageKey, titles)
}

export function removeSessionTitle(
  identityScopeKey: string,
  sessionId: string,
) {
  const storageKey = createSessionTitleStorageKey(identityScopeKey)
  const titles = { ...getTitlesSnapshot(storageKey) }
  delete titles[sessionId]
  writeTitles(storageKey, titles)
}

export function useSessionTitles(identityScopeKey: string) {
  const storageKey = createSessionTitleStorageKey(identityScopeKey)
  const subscribeToScope = useCallback(
    (onStoreChange: () => void) => subscribe(storageKey, onStoreChange),
    [storageKey],
  )
  const getSnapshot = useCallback(
    () => getTitlesSnapshot(storageKey),
    [storageKey],
  )
  const titles = useSyncExternalStore(
    subscribeToScope,
    getSnapshot,
    getSnapshot,
  )

  const getTitle = useCallback(
    (sessionId: string) => titles[sessionId] ?? sessionId,
    [titles],
  )
  const setTitle = useCallback(
    (sessionId: string, title: string) =>
      setSessionTitle(identityScopeKey, sessionId, title),
    [identityScopeKey],
  )
  const removeTitle = useCallback(
    (sessionId: string) => removeSessionTitle(identityScopeKey, sessionId),
    [identityScopeKey],
  )

  return { getTitle, removeTitle, setTitle, titles }
}
