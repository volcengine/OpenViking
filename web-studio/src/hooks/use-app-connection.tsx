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
    // Keep the URL as typed (whitespace trimmed only). Stripping the trailing
    // slash here ran on every keystroke and fought the input: typing the "//"
    // of "http://" kept collapsing back to "http:". Trailing slashes are
    // stripped where the URL is actually used instead (ovClient.setOptions,
    // detectServerMode, detectConnectionRole, and the admin client).
    baseUrl: connection.baseUrl.trim(),
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

export function resolveInitialApiKey({
  defaultApiKey,
  envApiKey,
  storedApiKey,
}: {
  defaultApiKey: string
  envApiKey: string
  storedApiKey: string | undefined
}): string {
  return envApiKey || storedApiKey || defaultApiKey
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

type ConnectionIdentity = {
  accountId: string
  role: ConnectionRole
}

async function detectConnectionIdentity(
  connection: ConnectionDraft,
): Promise<ConnectionIdentity> {
  const headers: Record<string, string> = {}
  const apiKey = connection.adminApiKey || connection.apiKey
  if (apiKey) {
    headers['X-API-Key'] = apiKey
  }

  const response = await fetch(
    `${normalizeBaseUrl(connection.baseUrl)}/health`,
    { headers },
  )
  if (!response.ok) {
    return { accountId: '', role: 'unknown' }
  }

  // /health resolves the presented key and echoes back its identity:
  // { role, account_id, user_id }. We use role to gate the admin UI and
  // account_id to pin the assumed account for an account-admin key.
  const data = (await response.json().catch(() => null)) as {
    account_id?: unknown
    role?: unknown
  } | null
  return {
    accountId: typeof data?.account_id === 'string' ? data.account_id : '',
    role: isConnectionRole(data?.role) ? data.role : 'unknown',
  }
}

function readInitialConnection(): ConnectionDraft {
  const storedConnection = readStoredConnection()
  const adminApiKey =
    ENV_ADMIN_API_KEY ||
    storedConnection.adminApiKey ||
    DEFAULT_CONNECTION.adminApiKey
  const apiKey = resolveInitialApiKey({
    defaultApiKey: DEFAULT_CONNECTION.apiKey,
    envApiKey: ENV_API_KEY,
    storedApiKey: storedConnection.apiKey,
  })
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

    void detectConnectionIdentity(connection)
      .then(({ accountId, role }) => {
        if (cancelled) {
          return
        }
        setConnectionRole(role)
        setConnectionRoleLoading(false)
        // An account-admin Root key is scoped to its own account. Pin that
        // account as the assumed identity so admin and data calls target the
        // right tenant instead of failing with a mismatch (the server rejects
        // a foreign account with "ADMIN can only manage account: <x>"). A root
        // key is not account-scoped, so its account selection is left intact.
        if (role === 'admin' && accountId) {
          setConnection((prev) =>
            prev.accountId === accountId ? prev : { ...prev, accountId },
          )
        }
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
