import * as React from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useRouterState } from '@tanstack/react-router'

import { isOvClientError, ovClient } from '#/lib/ov-client'

import { detectServerMode, normalizeBaseUrl } from './use-server-mode'
import type { ServerMode } from './use-server-mode'

export type ConnectionRole = 'admin' | 'root' | 'unknown' | 'user'

export type ConnectionDraft = {
  accountId: string
  adminApiKey: string
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
  connectionRole: ConnectionRole
  isConnectionRoleLoading: boolean
  openConnectionSettings: () => void
  saveConnection: (next: ConnectionDraft) => void
  serverMode: ServerMode
}

const CONNECTION_STORAGE_KEY = 'ov_console_connection'
const AUTH_PROMPT_SUPPRESSION_MS = 10000

const ENV_BASE_URL =
  typeof import.meta.env.VITE_OV_BASE_URL === 'string'
    ? import.meta.env.VITE_OV_BASE_URL.trim()
    : ''
const ENV_API_KEY =
  typeof import.meta.env.VITE_OV_API_KEY === 'string'
    ? import.meta.env.VITE_OV_API_KEY.trim()
    : ''
const ENV_ADMIN_API_KEY =
  typeof import.meta.env.VITE_OV_ADMIN_API_KEY === 'string'
    ? import.meta.env.VITE_OV_ADMIN_API_KEY.trim()
    : ''
const ENV_ACCOUNT =
  typeof import.meta.env.VITE_OV_ACCOUNT === 'string'
    ? import.meta.env.VITE_OV_ACCOUNT.trim()
    : ''
const ENV_USER =
  typeof import.meta.env.VITE_OV_USER === 'string'
    ? import.meta.env.VITE_OV_USER.trim()
    : ''

const DEFAULT_CONNECTION: ConnectionDraft = {
  accountId: ENV_ACCOUNT || 'default',
  adminApiKey: ENV_ADMIN_API_KEY,
  apiKey: ENV_API_KEY,
  baseUrl: ovClient.getOptions().baseUrl,
  userId: ENV_USER || 'default',
}

const AppConnectionContext =
  React.createContext<AppConnectionContextValue | null>(null)

function isBrowser(): boolean {
  return typeof window !== 'undefined'
}

function isConnectionRole(value: unknown): value is ConnectionRole {
  return (
    value === 'root' ||
    value === 'admin' ||
    value === 'user' ||
    value === 'unknown'
  )
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
    adminApiKey: connection.adminApiKey.trim(),
    apiKey: connection.apiKey.trim(),
    baseUrl: normalizeBaseUrl(connection.baseUrl),
    userId: connection.userId.trim(),
  }
}

function resolveIdentityField(
  envValue: string,
  storedValue: string | undefined,
  defaultValue: string,
): string {
  if (envValue) {
    return envValue
  }
  return storedValue || defaultValue
}

function applyConnection(
  connection: ConnectionDraft,
  serverMode: ServerMode,
): void {
  ovClient.setOptions({
    baseUrl: connection.baseUrl,
  })
  ovClient.setConnection({
    accountId: connection.accountId,
    adminApiKey: connection.adminApiKey,
    apiKey: connection.apiKey,
    identityHeaders: serverMode === 'trusted',
    userId: connection.userId,
  })
}

async function detectConnectionRole(
  connection: ConnectionDraft,
): Promise<ConnectionRole> {
  const headers: Record<string, string> = {}
  const apiKey = connection.adminApiKey || connection.apiKey
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(`${connection.baseUrl}/health`, { headers })
  if (!response.ok) {
    return 'unknown'
  }

  const data = (await response.json().catch(() => null)) as {
    role?: unknown
  } | null
  return isConnectionRole(data?.role) ? data.role : 'unknown'
}

function readInitialConnection(): ConnectionDraft {
  const storedConnection = readStoredConnection()
  const adminApiKey =
    ENV_ADMIN_API_KEY ||
    storedConnection.adminApiKey ||
    DEFAULT_CONNECTION.adminApiKey
  const apiKey =
    ENV_API_KEY ||
    ovClient.getConnection().apiKey ||
    storedConnection.apiKey ||
    DEFAULT_CONNECTION.apiKey
  return normalizeConnectionDraft({
    ...DEFAULT_CONNECTION,
    ...storedConnection,
    accountId: resolveIdentityField(
      ENV_ACCOUNT,
      storedConnection.accountId,
      DEFAULT_CONNECTION.accountId,
    ),
    adminApiKey,
    apiKey,
    baseUrl:
      ENV_BASE_URL || storedConnection.baseUrl || DEFAULT_CONNECTION.baseUrl,
    userId: resolveIdentityField(
      ENV_USER,
      storedConnection.userId,
      DEFAULT_CONNECTION.userId,
    ),
  })
}

export function summarizeConnectionIdentity(
  connection: ConnectionDraft,
  serverMode: ServerMode,
): ConnectionIdentitySummary {
  if (serverMode === 'dev') {
    return { labelKey: 'identitySummary.dev' }
  }

  const segments = [connection.accountId, connection.userId].filter(Boolean)
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
  const authPromptSuppressedUntilRef = React.useRef(0)
  const navigate = useNavigate()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const initialConnectionRef = React.useRef<ConnectionDraft | null>(null)
  if (initialConnectionRef.current === null) {
    initialConnectionRef.current = readInitialConnection()
    applyConnection(initialConnectionRef.current, 'checking')
  }

  const [connection, setConnection] = React.useState<ConnectionDraft>(
    initialConnectionRef.current,
  )
  const [connectionRole, setConnectionRole] =
    React.useState<ConnectionRole>('unknown')
  const [isConnectionRoleLoading, setConnectionRoleLoading] = React.useState(
    () =>
      Boolean(
        initialConnectionRef.current?.baseUrl &&
          (initialConnectionRef.current.adminApiKey ||
            initialConnectionRef.current.apiKey),
      ),
  )
  const [serverMode, setServerMode] = React.useState<ServerMode>('checking')

  const openConnectionSettings = React.useCallback(() => {
    if (pathname !== '/settings') {
      void navigate({ to: '/settings' })
    }
  }, [navigate, pathname])

  React.useEffect(() => {
    applyConnection(connection, serverMode)
    persistConnection(connection)
    void queryClient.invalidateQueries()
  }, [connection, queryClient, serverMode])

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
    let cancelled = false
    const apiKey = connection.adminApiKey || connection.apiKey

    setConnectionRole('unknown')
    setConnectionRoleLoading(Boolean(connection.baseUrl && apiKey))
    if (!connection.baseUrl || !apiKey) {
      return () => {
        cancelled = true
      }
    }

    void detectConnectionRole(connection)
      .then((role) => {
        if (cancelled) {
          return
        }
        setConnectionRole(role)
        setConnectionRoleLoading(false)
      })
      .catch(() => {
        if (!cancelled) {
          setConnectionRole('unknown')
          setConnectionRoleLoading(false)
        }
      })

    return () => {
      cancelled = true
    }
  }, [
    connection.accountId,
    connection.adminApiKey,
    connection.apiKey,
    connection.baseUrl,
    connection.userId,
  ])

  React.useEffect(() => {
    const interceptorId = ovClient.instance.interceptors.response.use(
      (response) => response,
      (error) => {
        if (
          isOvClientError(error) &&
          (error.statusCode === 401 || error.statusCode === 403) &&
          Date.now() >= authPromptSuppressedUntilRef.current
        ) {
          openConnectionSettings()
        }
        return Promise.reject(error)
      },
    )

    return () => {
      ovClient.instance.interceptors.response.eject(interceptorId)
    }
  }, [openConnectionSettings])

  const value = React.useMemo<AppConnectionContextValue>(
    () => ({
      connection,
      connectionRole,
      isConnectionRoleLoading,
      openConnectionSettings,
      saveConnection: (next) => {
        authPromptSuppressedUntilRef.current =
          Date.now() + AUTH_PROMPT_SUPPRESSION_MS
        void queryClient.cancelQueries()
        setConnection(normalizeConnectionDraft(next))
      },
      serverMode,
    }),
    [
      connection,
      connectionRole,
      isConnectionRoleLoading,
      openConnectionSettings,
      queryClient,
      serverMode,
    ],
  )

  return (
    <AppConnectionContext.Provider value={value}>
      {children}
    </AppConnectionContext.Provider>
  )
}
