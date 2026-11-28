import { useCallback, useEffect, useRef, useState } from 'react'

export function useApi<T>(loader: () => Promise<T>, key: string, pollMs = 0) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const generation = useRef(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  const reload = useCallback(async (quiet = false) => {
    const current = ++generation.current
    if (quiet) setRefreshing(true)
    else setLoading(true)
    try {
      const value = await loaderRef.current()
      if (generation.current !== current) return
      setData(value)
      setError(null)
    } catch (reason) {
      if (generation.current !== current) return
      setError(reason instanceof Error ? reason : new Error(String(reason)))
    } finally {
      if (generation.current === current) {
        setLoading(false)
        setRefreshing(false)
      }
    }
  }, [])

  useEffect(() => {
    setData(null)
    void reload()
    if (!pollMs) return
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void reload(true)
    }, pollMs)
    return () => {
      generation.current += 1
      window.clearInterval(timer)
    }
  }, [key, pollMs, reload])

  return { data, error, loading, refreshing, reload: () => reload(true) }
}

export type Route = { page: string; taskId?: string }

export function useHashRoute() {
  const parse = (): Route => {
    const parts = window.location.hash.replace(/^#\/?/, '').split('/').filter(Boolean)
    if (parts[0] === 'tasks' && parts[1]) return { page: 'task', taskId: decodeURIComponent(parts[1]) }
    return { page: parts[0] || 'overview' }
  }
  const [route, setRoute] = useState<Route>(parse)
  useEffect(() => {
    const update = () => setRoute(parse())
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])
  return route
}
