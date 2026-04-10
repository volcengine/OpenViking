import * as React from 'react'

import { isOvClientError, ovClient } from '#/lib/ov-client'

import { detectServerMode, normalizeBaseUrl, type ServerMode } from './use-server-mode'

export type ConnectionDraft = {
  accountId: string
  apiKey: string
  baseUrl: string
  userId: string
}

type AppConnectionContextValue = {
  connection: ConnectionDraft
  isConnectionDialogOpen: boolean
  openConnectionDialog: () => void
  saveConnection: (next: ConnectionDraft) => void
  serverMode: ServerMode
  setConnectionDialogOpen: (open: boolean) => void
}

const CONNECTION_STORAGE_KEY = 'ov_console_connection'

const DEFAULT_CONNECTION: ConnectionDraft = {
  accountId: '',
  apiKey: '',
  baseUrl: ovClient.getOptions().baseUrl,
  userId: '',
}

const AppConnectionContext = React.createContext<AppConnectionContextValue | null>(null)

function isBrowser(): boolean {
  return typeof window !== 'undefined'
}

function readStoredConnection(): Partial<ConnectionDraft> {
  if (!isBrowser()) {
    return {}
  }

  try {
    const raw = window.localStorage.getItem(CONNECTION_STORAGE_KEY)
    if (!raw) {
      return {}
    }
    const parsed = JSON.parse(raw) as Partial<ConnectionDraft>
    return typeof parsed === 'object' && parsed !== null ? parsed : {}
  } catch {
    return {}
  }
}

function persistConnection(connection: ConnectionDraft): void {
  if (!isBrowser()) {
    return
  }

  try {
    window.localStorage.setItem(CONNECTION_STORAGE_KEY, JSON.stringify(connection))
  } catch {
    // Ignore localStorage failures in restricted environments.
  }
}

export function summarizeConnectionIdentity(connection: ConnectionDraft, serverMode: ServerMode): string {
  if (serverMode === 'dev-implicit') {
    return '服务端隐式身份'
  }

  const segments = [connection.accountId, connection.userId].filter(Boolean)
  if (!segments.length) {
    return '未设置身份'
  }

  return segments.join(' / ')
}

export function useAppConnection(): AppConnectionContextValue {
  const context = React.useContext(AppConnectionContext)
  if (!context) {
    throw new Error('useAppConnection must be used within AppConnectionProvider.')
  }

  return context
}

export function AppConnectionProvider({ children }: { children: React.ReactNode }) {
  const storedConnection = React.useMemo(() => readStoredConnection(), [])
  const [connection, setConnection] = React.useState<ConnectionDraft>({
    ...DEFAULT_CONNECTION,
    ...storedConnection,
    apiKey: ovClient.getConnection().apiKey || storedConnection.apiKey || DEFAULT_CONNECTION.apiKey,
  })
  const [isConnectionDialogOpen, setConnectionDialogOpen] = React.useState(false)
  const [serverMode, setServerMode] = React.useState<ServerMode>('checking')

  React.useEffect(() => {
    ovClient.setOptions({
      baseUrl: connection.baseUrl,
    })
    ovClient.setConnection({
      accountId: connection.accountId,
      apiKey: connection.apiKey,
      userId: connection.userId,
    })
    persistConnection(connection)
  }, [connection])

  React.useEffect(() => {
    let cancelled = false

    setServerMode('checking')
    void detectServerMode(connection.baseUrl).then((mode) => {
      if (!cancelled) {
        setServerMode(mode)
      }
    })

    return () => {
      cancelled = true
    }
  }, [connection.baseUrl])

  React.useEffect(() => {
    const interceptorId = ovClient.instance.interceptors.response.use(
      (response) => response,
      (error) => {
        if (isOvClientError(error) && (error.statusCode === 401 || error.statusCode === 403)) {
          setConnectionDialogOpen(true)
        }
        return Promise.reject(error)
      },
    )

    return () => {
      ovClient.instance.interceptors.response.eject(interceptorId)
    }
  }, [])

  const value = React.useMemo<AppConnectionContextValue>(() => ({
    connection,
    isConnectionDialogOpen,
    openConnectionDialog: () => setConnectionDialogOpen(true),
    saveConnection: (next) => setConnection({
      accountId: next.accountId.trim(),
      apiKey: next.apiKey.trim(),
      baseUrl: normalizeBaseUrl(next.baseUrl),
      userId: next.userId.trim(),
    }),
    serverMode,
    setConnectionDialogOpen,
  }), [connection, isConnectionDialogOpen, serverMode])

  return (
    <AppConnectionContext.Provider value={value}>
      {children}
    </AppConnectionContext.Provider>
  )
}