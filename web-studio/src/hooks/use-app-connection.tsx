import * as React from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { isOvClientError, ovClient } from '#/lib/ov-client'

import { detectServerMode, normalizeBaseUrl } from './use-server-mode'
import type { ServerMode } from './use-server-mode'

export type ConnectionDraft = {
  accountId: string
  agentId: string
  apiKey: string
  baseUrl: string
  userId: string
}

export type ConnectionIdentitySummary = {
  labelKey: string
  values?: {
    identity?: string
  }
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

const ENV_BASE_URL =
  typeof import.meta.env.VITE_OV_BASE_URL === 'string'
    ? import.meta.env.VITE_OV_BASE_URL.trim()
    : ''
const ENV_API_KEY =
  typeof import.meta.env.VITE_OV_API_KEY === 'string'
    ? import.meta.env.VITE_OV_API_KEY.trim()
    : ''
const ENV_ACCOUNT =
  typeof import.meta.env.VITE_OV_ACCOUNT === 'string'
    ? import.meta.env.VITE_OV_ACCOUNT.trim()
    : ''
const ENV_AGENT =
  typeof import.meta.env.VITE_OV_AGENT === 'string'
    ? import.meta.env.VITE_OV_AGENT.trim()
    : ''
const ENV_USER =
  typeof import.meta.env.VITE_OV_USER === 'string'
    ? import.meta.env.VITE_OV_USER.trim()
    : ''

const DEFAULT_CONNECTION: ConnectionDraft = {
  accountId: ENV_ACCOUNT || 'default',
  agentId: ENV_AGENT || 'web-studio',
  apiKey: ENV_API_KEY,
  baseUrl: ovClient.getOptions().baseUrl,
  userId: ENV_USER || 'default',
}

const AppConnectionContext =
  React.createContext<AppConnectionContextValue | null>(null)

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
    const parsed: unknown = JSON.parse(raw)
    return typeof parsed === 'object' && parsed !== null
      ? (parsed as Partial<ConnectionDraft>)
      : {}
  } catch {
    return {}
  }
}

function persistConnection(connection: ConnectionDraft): void {
  if (!isBrowser()) {
    return
  }

  try {
    window.localStorage.setItem(
      CONNECTION_STORAGE_KEY,
      JSON.stringify(connection),
    )
  } catch {
    // Ignore localStorage failures in restricted environments.
  }
}

function normalizeConnectionDraft(
  connection: ConnectionDraft,
): ConnectionDraft {
  return {
    accountId: connection.accountId.trim(),
    agentId: connection.agentId.trim() || DEFAULT_CONNECTION.agentId,
    apiKey: connection.apiKey.trim(),
    baseUrl: normalizeBaseUrl(connection.baseUrl),
    userId: connection.userId.trim(),
  }
}

function applyConnection(connection: ConnectionDraft): void {
  ovClient.setOptions({
    baseUrl: connection.baseUrl,
  })
  ovClient.setConnection({
    accountId: connection.accountId,
    agentId: connection.agentId,
    apiKey: connection.apiKey,
    userId: connection.userId,
  })
}

function readInitialConnection(): ConnectionDraft {
  const storedConnection = readStoredConnection()
  return normalizeConnectionDraft({
    ...DEFAULT_CONNECTION,
    ...storedConnection,
    accountId:
      ENV_ACCOUNT || storedConnection.accountId || DEFAULT_CONNECTION.accountId,
    agentId:
      ENV_AGENT || storedConnection.agentId || DEFAULT_CONNECTION.agentId,
    apiKey:
      ENV_API_KEY ||
      ovClient.getConnection().apiKey ||
      storedConnection.apiKey ||
      DEFAULT_CONNECTION.apiKey,
    baseUrl:
      ENV_BASE_URL || storedConnection.baseUrl || DEFAULT_CONNECTION.baseUrl,
    userId: ENV_USER || storedConnection.userId || DEFAULT_CONNECTION.userId,
  })
}

export function summarizeConnectionIdentity(
  connection: ConnectionDraft,
  serverMode: ServerMode,
): ConnectionIdentitySummary {
  if (serverMode === 'dev') {
    return { labelKey: 'identitySummary.dev' }
  }

  const segments = [
    connection.accountId,
    connection.userId,
    connection.agentId,
  ].filter(Boolean)
  if (!segments.length) {
    return { labelKey: 'identitySummary.unset' }
  }

  return {
    labelKey: 'identitySummary.named',
    values: {
      identity: segments.join(' / '),
    },
  }
}

export function useAppConnection(): AppConnectionContextValue {
  const context = React.useContext(AppConnectionContext)
  if (!context) {
    throw new Error(
      'useAppConnection must be used within AppConnectionProvider.',
    )
  }

  return context
}

export function AppConnectionProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const queryClient = useQueryClient()
  const initialConnectionRef = React.useRef<ConnectionDraft | null>(null)
  if (initialConnectionRef.current === null) {
    initialConnectionRef.current = readInitialConnection()
    applyConnection(initialConnectionRef.current)
  }

  const [connection, setConnection] = React.useState<ConnectionDraft>(
    initialConnectionRef.current,
  )
  const [isConnectionDialogOpen, setConnectionDialogOpen] =
    React.useState(false)
  const [serverMode, setServerMode] = React.useState<ServerMode>('checking')

  React.useEffect(() => {
    applyConnection(connection)
    persistConnection(connection)
    void queryClient.invalidateQueries()
  }, [connection, queryClient])

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
        if (
          isOvClientError(error) &&
          (error.statusCode === 401 || error.statusCode === 403)
        ) {
          setConnectionDialogOpen(true)
        }
        return Promise.reject(error)
      },
    )

    return () => {
      ovClient.instance.interceptors.response.eject(interceptorId)
    }
  }, [])

  const value = React.useMemo<AppConnectionContextValue>(
    () => ({
      connection,
      isConnectionDialogOpen,
      openConnectionDialog: () => setConnectionDialogOpen(true),
      saveConnection: (next) => setConnection(normalizeConnectionDraft(next)),
      serverMode,
      setConnectionDialogOpen,
    }),
    [connection, isConnectionDialogOpen, serverMode],
  )

  return (
    <AppConnectionContext.Provider value={value}>
      {children}
    </AppConnectionContext.Provider>
  )
}
