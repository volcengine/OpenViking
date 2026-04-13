import { useSyncExternalStore, useCallback } from 'react'

const STORAGE_KEY = 'ov-session-titles'

function readTitles(): Record<string, string> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Record<string, string>) : {}
  } catch {
    return {}
  }
}

function writeTitles(titles: Record<string, string>) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(titles))
  // Notify all subscribers within the same tab
  window.dispatchEvent(new StorageEvent('storage', { key: STORAGE_KEY }))
}

// Shared snapshot reference — updated on every storage event
let snapshot = readTitles()

function subscribe(onStoreChange: () => void) {
  const handler = (e: StorageEvent) => {
    if (e.key === STORAGE_KEY || e.key === null) {
      snapshot = readTitles()
      onStoreChange()
    }
  }
  window.addEventListener('storage', handler)
  return () => window.removeEventListener('storage', handler)
}

function getSnapshot() {
  return snapshot
}

export function setSessionTitle(sessionId: string, title: string) {
  const titles = readTitles()
  titles[sessionId] = title
  writeTitles(titles)
  snapshot = titles
}

export function removeSessionTitle(sessionId: string) {
  const titles = readTitles()
  delete titles[sessionId]
  writeTitles(titles)
  snapshot = titles
}

export function useSessionTitles() {
  const titles = useSyncExternalStore(subscribe, getSnapshot, getSnapshot)

  const getTitle = useCallback(
    (sessionId: string) => titles[sessionId] ?? sessionId,
    [titles],
  )

  return { titles, getTitle }
}
