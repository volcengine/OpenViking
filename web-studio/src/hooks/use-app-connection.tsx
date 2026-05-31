import * as React from 'react'
import { useQueryClient } from '@tanstack/react-query'

import { isOvClientError, ovClient } from '#/lib/ov-client'
import { getStudioRuntime } from '#/lib/studio-runtime'

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
  proxyMode: boolean
  saveConnection: (next: ConnectionDraft) => void
  serverMode: ServerMode
  setConnectionDialogOpen: (open: boolean) => void
}

const CONNECTION_STORAGE_KEY = 'ov_console_connection'

const DEFAULT_CONNECTION: ConnectionDraft = {
  accountId: 'default',
  agentId: 'web-studio',
  apiKey: '',
  baseUrl: ovClient.getOptions().baseUrl,
  userId: 'default',
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

function persistConnection(
  connection: ConnectionDraft,
  proxyMode: boolean,
): void {
  if (!isBrowser() || proxyMode) {
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

function buildProxyConnection(): ConnectionDraft {
  return {
    accountId: '',
    agentId: DEFAULT_CONNECTION.agentId,
    apiKey: '',
    baseUrl: isBrowser() ? window.location.origin : '',
    userId: '',
  }
}

function normalizeConnectionDraft(connection: ConnectionDraft): ConnectionDraft {
  return {
    accountId: connection.accountId.trim(),
    agentId: connection.agentId.trim() || DEFAULT_CONNECTION.agentId,
    apiKey: connection.apiKey.trim(),
    baseUrl: normalizeBaseUrl(connection.baseUrl),
    userId: connection.userId.trim(),
  }
}

function applyConnection(
  connection: ConnectionDraft,
  proxyMode: boolean,
): void {
  ovClient.setOptions({
    baseUrl: connection.baseUrl,
    proxyMode,
  })
  ovClient.setConnection({
    accountId: proxyMode ? '' : connection.accountId,
    agentId: connection.agentId,
    apiKey: proxyMode ? '' : connection.apiKey,
    userId: proxyMode ? '' : connection.userId,
  })
}

function readInitialConnection(proxyMode: boolean): ConnectionDraft {
  if (proxyMode) {
    return normalizeConnectionDraft(buildProxyConnection())
  }
  const storedConnection = readStoredConnection()
  return normalizeConnectionDraft({
    ...DEFAULT_CONNECTION,
    ...storedConnection,
    apiKey:
      ovClient.getConnection().apiKey ||
      storedConnection.apiKey ||
      DEFAULT_CONNECTION.apiKey,
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
  const proxyMode = getStudioRuntime().proxyMode
  const initialConnectionRef = React.useRef<ConnectionDraft | null>(null)
  if (initialConnectionRef.current === null) {
    initialConnectionRef.current = readInitialConnection(proxyMode)
    applyConnection(initialConnectionRef.current, proxyMode)
  }

  const [connection, setConnection] = React.useState<ConnectionDraft>(
    initialConnectionRef.current,
  )
  const [isConnectionDialogOpen, setConnectionDialogOpen] =
    React.useState(false)
  const [serverMode, setServerMode] = React.useState<ServerMode>('checking')

  React.useEffect(() => {
    applyConnection(connection, proxyMode)
    persistConnection(connection, proxyMode)
    void queryClient.invalidateQueries()
  }, [connection, proxyMode, queryClient])

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
          !proxyMode &&
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
  }, [proxyMode])

  const value = React.useMemo<AppConnectionContextValue>(
    () => ({
      connection,
      isConnectionDialogOpen,
      openConnectionDialog: () => setConnectionDialogOpen(true),
      proxyMode,
      saveConnection: (next) => {
        if (proxyMode) {
          return
        }
        setConnection(normalizeConnectionDraft(next))
      },
      serverMode,
      setConnectionDialogOpen,
    }),
    [connection, isConnectionDialogOpen, proxyMode, serverMode],
  )

  return (
    <AppConnectionContext.Provider value={value}>
      {children}
    </AppConnectionContext.Provider>
  )
}
