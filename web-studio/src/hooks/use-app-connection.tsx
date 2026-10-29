import {
  resolveApiKeyAuth,
  createConnectionHealthHeaders,
} from '#/lib/connection/auth'
import * as React from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useNavigate, useRouterState } from '@tanstack/react-router'

import i18n from '#/i18n'
import { identifyServiceProvider } from '#/lib/studio-service'
import { fetchAdminAccounts } from '#/lib/admin'
import {
  isOvClientError,
  normalizeOvClientError,
  ovClient,
} from '#/lib/ov-client'
import {
  readConnectionProfiles,
  activateIdentityProfile,
  persistConnectionProfiles,
  updateActiveProfile,
  normalizeProfileConnection,
  isValidConnectionUrl,
} from '#/lib/connection/profiles'
import type {
  ConnectionProfile,
  ConnectionProfiles,
} from '#/lib/connection/profiles'

import {
  resolveServerAuthMode,
  resetServerHealthCache,
  fetchServerHealth,
  normalizeBaseUrl,
  readServerIdentity,
} from '#/lib/connection/health'
import type { ServerMode } from '#/lib/connection/health'

import type { ConnectionDraft, ConnectionRole } from '#/lib/connection/types'

export type { ConnectionDraft, ConnectionRole } from '#/lib/connection/types'
export { createConnectionHealthHeaders } from '#/lib/connection/auth'

export type ConnectionIdentitySummary = {
  labelKey: string
  values?: {
    identity?: string
  }
}

export type GeneratedCredential = {
  accountId?: string
  apiKey: string
  userId?: string
}

type AppConnectionContextValue = {
  profiles: Array<ConnectionProfile>
  activeProfileId: string
  saveProfile: (profile: ConnectionProfile) => void
  activateProfile: (id: string) => void
  deleteProfile: (id: string) => void
  clearGeneratedCredential: () => void
  connection: ConnectionDraft
  connectionRole: ConnectionRole
  generatedCredential: GeneratedCredential | null
  identityScopeKey: string
  isConnectionRoleLoading: boolean
  isGatewayHost: boolean
  openvikingUrl: string
  openConnectionSettings: () => void
  reconnect: () => void
  serverHealth: Record<string, unknown> | null
  saveConnection: (next: ConnectionDraft) => void
  setGeneratedCredential: (credential: GeneratedCredential) => void
  serverMode: ServerMode
  switchIdentity: (identity: {
    accountId: string
    allowLegacyIdentityFallback?: boolean
    apiKey: string
    userId: string
  }) => Promise<void>
  switchManagementAccount: (accountId: string) => Promise<void>
}

const CONNECTION_STORAGE_KEY = 'ov_console_connection'
const AUTH_PROMPT_SUPPRESSION_MS = 10000

type ConnectionErrorKey =
  | 'credentialMismatch'
  | 'identitySwitchUnsupported'
  | 'rootRequired'

export function createConnectionError(key: ConnectionErrorKey): Error {
  return new Error(i18n.t(`connection:errors.${key}`))
}

function connectionChangedError(): Error & { code: 'connectionChanged' } {
  return Object.assign(new Error(i18n.t('studio:profiles.connectionChanged')), {
    code: 'connectionChanged' as const,
  })
}

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
    serviceSelection:
      connection.serviceSelection === 'opensource' ||
      connection.serviceSelection === 'volcengine'
        ? connection.serviceSelection
        : 'auto',
    accountId: connection.accountId.trim(),
    adminApiKey: connection.adminApiKey.trim(),
    apiKey: connection.apiKey.trim(),
    // Keep the URL as typed (whitespace trimmed only). Stripping the trailing
    // slash here ran on every keystroke and fought the input: typing the "//"
    // of "http://" kept collapsing back to "http:". Trailing slashes are
    // stripped where the URL is actually used instead (ovClient.setOptions,
    // fetchServerHealth, detectConnectionRole, and the admin client).
    baseUrl: connection.baseUrl.trim(),
    userId: connection.userId.trim(),
  }
}

function hashSecret(value: string): string {
  let hash = 0x811c9dc5
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 0x01000193)
  }
  return (hash >>> 0).toString(36)
}

export function createIdentityScopeKey(
  connection: ConnectionDraft,
  serverMode: ServerMode,
): string {
  const dataKey = connection.apiKey || connection.adminApiKey
  return [
    normalizeBaseUrl(connection.baseUrl),
    connection.serviceSelection ?? 'auto',
    serverMode,
    connection.accountId,
    connection.userId,
    dataKey ? hashSecret(dataKey) : 'none',
  ].join('\u0000')
}

export function createConnectionRoleProbeKey(
  connection: ConnectionDraft,
  serverMode: ServerMode,
): string {
  return [
    normalizeBaseUrl(connection.baseUrl),
    connection.serviceSelection ?? 'auto',
    serverMode,
    connection.accountId,
    connection.userId,
    connection.adminApiKey ? hashSecret(connection.adminApiKey) : 'none',
    connection.apiKey ? hashSecret(connection.apiKey) : 'none',
  ].join('\u0000')
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

export function resolveConnectionRoleProbeState({
  apiKey,
  baseUrl,
  serverMode,
}: {
  apiKey: string
  baseUrl: string
  serverMode: ServerMode
}): {
  isLoading: boolean
  role: ConnectionRole
  shouldProbe: boolean
} {
  if (!baseUrl || serverMode === 'offline') {
    return { isLoading: false, role: 'unknown', shouldProbe: false }
  }
  if (serverMode === 'checking') {
    return { isLoading: true, role: 'unknown', shouldProbe: false }
  }
  if (serverMode === 'dev') {
    return { isLoading: false, role: 'root', shouldProbe: false }
  }
  if (!apiKey) {
    return { isLoading: false, role: 'unknown', shouldProbe: false }
  }
  return { isLoading: true, role: 'unknown', shouldProbe: true }
}

export function shouldRedirectToLoginOnApiError(
  error: unknown,
  isClientError: (value: unknown) => boolean = isOvClientError,
): boolean {
  if (!isClientError(error)) {
    return false
  }

  const clientError = error as { code?: string; statusCode?: number }
  return (
    clientError.statusCode === 401 || clientError.code === 'UNAUTHENTICATED'
  )
}

function applyConnection(
  connection: ConnectionDraft,
  serverMode: ServerMode,
): void {
  ovClient.setOptions({
    baseUrl: connection.baseUrl,
    apiKeyAuth: resolveApiKeyAuth(
      connection.baseUrl,
      connection.serviceSelection,
    ),
  })
  ovClient.setConnection({
    accountId: connection.accountId,
    adminApiKey: connection.adminApiKey,
    apiKey: connection.apiKey,
    identityHeaders: serverMode === 'trusted' || serverMode === 'dev',
    userId: connection.userId,
  })
}

export function synchronizeConnectionRuntime(
  connection: ConnectionDraft,
  serverMode: ServerMode,
): ConnectionDraft {
  const normalized = normalizeConnectionDraft(connection)
  // Keep the imperative request client ahead of the React tree. Identity
  // changes remount route content, whose child effects may start requests
  // before this provider's passive effects run.
  applyConnection(normalized, serverMode)
  persistConnection(normalized)
  return normalized
}

type ConnectionIdentity = {
  accountId: string
  role: ConnectionRole
  userId: string
}

async function canListAccounts(connection: ConnectionDraft): Promise<boolean> {
  if (
    !connection.adminApiKey ||
    identifyServiceProvider(connection.baseUrl) === 'volcengine' ||
    connection.serviceSelection === 'volcengine'
  ) {
    return false
  }

  try {
    await fetchAdminAccounts({
      accountId: connection.accountId,
      apiKey: connection.adminApiKey,
      baseUrl: connection.baseUrl,
      userId: connection.userId,
    })
    return true
  } catch {
    return false
  }
}

async function detectConnectionIdentity(
  connection: ConnectionDraft,
  credential: 'control' | 'data' = 'control',
): Promise<ConnectionIdentity> {
  const data = await fetchServerHealth(
    connection.baseUrl,
    createConnectionHealthHeaders(connection, credential),
  )

  // /health resolves the presented key and echoes back its identity:
  // { role, account_id, user_id }. We use role to gate the admin UI and
  // account_id to pin the assumed account for an account-admin key.
  const healthRole = isConnectionRole(data.role) ? data.role : 'unknown'
  // In trusted mode /health resolves the asserted tenant user, even when the
  // configured Root key is the credential authorizing Admin API calls. Probe
  // the actual control-plane endpoint so Root capabilities reflect what the
  // browser can really do.
  const role =
    credential === 'control' &&
    connection.adminApiKey &&
    (await canListAccounts(connection))
      ? 'root'
      : healthRole

  return {
    ...readServerIdentity(data),
    role,
  }
}

export function synchronizeResolvedDataIdentity(
  connection: ConnectionDraft,
  identity: ConnectionIdentity,
): ConnectionDraft | null {
  if (
    (!connection.apiKey && Boolean(connection.adminApiKey)) ||
    (identity.role !== 'admin' && identity.role !== 'user') ||
    !identity.accountId ||
    !identity.userId ||
    (connection.accountId === identity.accountId &&
      connection.userId === identity.userId)
  ) {
    return null
  }

  return {
    ...connection,
    accountId: identity.accountId,
    userId: identity.userId,
  }
}

export function resolveSwitchedIdentity(
  requested: Pick<ConnectionDraft, 'accountId' | 'userId'>,
  identity: ConnectionIdentity,
  allowLegacyIdentityFallback = false,
): Pick<ConnectionDraft, 'accountId' | 'userId'> | null {
  if (
    (identity.role === 'unknown' && !allowLegacyIdentityFallback) ||
    (identity.accountId && identity.accountId !== requested.accountId) ||
    (identity.userId &&
      requested.userId &&
      identity.userId !== requested.userId)
  ) {
    return null
  }

  return {
    accountId: identity.accountId || requested.accountId,
    userId: identity.userId || requested.userId,
  }
}

export function createManagementAccountConnection(
  connection: ConnectionDraft,
  accountId: string,
  isGatewayHost = false,
): ConnectionDraft {
  if (isGatewayHost) {
    return { ...connection, accountId: accountId.trim() }
  }
  return {
    ...connection,
    accountId: accountId.trim(),
    apiKey: '',
    userId: '',
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
    // eslint-disable-next-line studio-i18n/no-literal-message -- Developer invariant; retain the diagnostic for debugging.
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
  const connectionRevisionRef = React.useRef(0)
  const connectionGenerationRef = React.useRef(0)
  const hostDiscoveryRef = React.useRef<Promise<Record<
    string,
    unknown
  > | null> | null>(null)
  const navigate = useNavigate()
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const initialConnectionRef = React.useRef<ConnectionDraft | null>(null)
  const profilesRef = React.useRef<ConnectionProfiles | null>(null)
  const synchronizedRoleProbeRef = React.useRef<{
    key: string
    role: ConnectionRole
  } | null>(null)
  if (initialConnectionRef.current === null) {
    // The provider remounts after /config. Filesystem query keys are shared
    // across profiles, so discard results fetched under the previous identity
    // before any workspace route can read them.
    queryClient.clear()
    profilesRef.current = readConnectionProfiles(readInitialConnection())
    initialConnectionRef.current = normalizeConnectionDraft(
      profilesRef.current.profiles.find(
        (p) => p.id === profilesRef.current!.activeId,
      )!.connection,
    )
    applyConnection(initialConnectionRef.current, 'checking')
  }

  const [profileState, setProfileState] = React.useState(profilesRef.current!)
  const storeProfiles = React.useCallback((next: ConnectionProfiles) => {
    profilesRef.current = next
    persistConnectionProfiles(next)
    setProfileState(next)
  }, [])

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
  const [serverHealth, setServerHealth] = React.useState<{
    key: string
    data: Record<string, unknown>
  } | null>(null)
  const [connectionAttempt, setConnectionAttempt] = React.useState(0)
  const reconnect = React.useCallback(() => {
    connectionRevisionRef.current += 1
    authPromptSuppressedUntilRef.current =
      Date.now() + AUTH_PROMPT_SUPPRESSION_MS
    synchronizedRoleProbeRef.current = null
    resetServerHealthCache()
    setServerHealth(null)
    setServerMode('checking')
    setConnectionRole('unknown')
    setConnectionRoleLoading(true)
    queryClient.clear()
    setConnectionAttempt((attempt) => attempt + 1)
  }, [queryClient])
  const [isGatewayHost, setIsGatewayHost] = React.useState(false)
  const [openvikingUrl, setOpenvikingUrl] = React.useState('')
  const [generatedCredential, setGeneratedCredential] =
    React.useState<GeneratedCredential | null>(null)

  const openConnectionSettings = React.useCallback(() => {
    if (pathname !== '/settings') {
      void navigate({ to: '/settings' })
    }
  }, [navigate, pathname])
  const onBootstrapAuthError = React.useEffectEvent(() => {
    openConnectionSettings()
  })

  React.useEffect(() => {
    applyConnection(connection, serverMode)
    persistConnection(connection)
  }, [connection, serverMode])

  React.useEffect(() => {
    storeProfiles(updateActiveProfile(profilesRef.current!, connection))
  }, [connection, storeProfiles])

  React.useEffect(() => {
    let cancelled = false

    const isCancelled = () => cancelled
    setServerMode('checking')
    setServerHealth(null)
    void (async () => {
      // A standalone Studio is bound to the gateway serving /studio. Discover
      // that host without forwarding credentials saved for another server.
      if (
        isBrowser() &&
        window.location.pathname.startsWith('/studio') &&
        normalizeBaseUrl(connection.baseUrl) !== window.location.origin
      ) {
        hostDiscoveryRef.current ??= fetchServerHealth(
          window.location.origin,
        ).catch(() => null)
        const host = await hostDiscoveryRef.current
        if (isCancelled()) return
        if (host?.service === 'openviking-gateway') {
          setIsGatewayHost(true)
          setOpenvikingUrl(
            typeof host.openviking_url === 'string' ? host.openviking_url : '',
          )
          const next = synchronizeConnectionRuntime(
            {
              ...connection,
              serviceSelection: 'opensource',
              baseUrl: window.location.origin,
              apiKey: '',
              adminApiKey: '',
              accountId: 'default',
              userId: 'default',
            },
            'checking',
          )
          queryClient.clear()
          connectionGenerationRef.current += 1
          setConnection(next)
          return
        }
      }
      let health: Record<string, unknown>
      try {
        // The gateway answers an invalid OV key with health minus identity.
        health = await fetchServerHealth(
          connection.baseUrl,
          createConnectionHealthHeaders(connection, 'data'),
        )
      } catch (error) {
        if (!isCancelled()) {
          setServerMode('offline')
          if (shouldRedirectToLoginOnApiError(normalizeOvClientError(error))) {
            onBootstrapAuthError()
          }
        }
        return
      }
      if (!isCancelled()) {
        setIsGatewayHost(health.service === 'openviking-gateway')
        setOpenvikingUrl(
          typeof health.openviking_url === 'string'
            ? health.openviking_url
            : '',
        )
        const mode = resolveServerAuthMode(health)
        const resolved = synchronizeResolvedDataIdentity(connection, {
          accountId:
            typeof health.account_id === 'string' ? health.account_id : '',
          userId: typeof health.user_id === 'string' ? health.user_id : '',
          role: isConnectionRole(health.role) ? health.role : 'unknown',
        })
        const next = resolved
          ? synchronizeConnectionRuntime(resolved, mode)
          : connection
        if (resolved) {
          queryClient.clear()
          connectionGenerationRef.current += 1
          setConnection(next)
        }
        setServerHealth({
          key: createConnectionRoleProbeKey(next, 'checking'),
          data: health,
        })
        setServerMode(mode)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [
    connection.accountId,
    connection.adminApiKey,
    connection.apiKey,
    connection.baseUrl,
    connection.serviceSelection,
    connection.userId,
    connectionAttempt,
    queryClient,
  ])

  React.useEffect(() => {
    let cancelled = false
    const isCancelled = () => cancelled
    const apiKey = connection.adminApiKey || connection.apiKey
    const roleProbe = resolveConnectionRoleProbeState({
      apiKey,
      baseUrl: connection.baseUrl,
      serverMode,
    })
    const probeKey = createConnectionRoleProbeKey(connection, serverMode)
    const synchronizedProbe = synchronizedRoleProbeRef.current

    if (synchronizedProbe?.key === probeKey) {
      synchronizedRoleProbeRef.current = null
      setConnectionRole(synchronizedProbe.role)
      setConnectionRoleLoading(false)
      return () => {
        cancelled = true
      }
    }

    setConnectionRole(roleProbe.role)
    setConnectionRoleLoading(roleProbe.isLoading)
    if (!roleProbe.shouldProbe) {
      return () => {
        cancelled = true
      }
    }

    void detectConnectionIdentity(connection)
      .catch(() => ({
        accountId: '',
        role: 'unknown' as const,
        userId: '',
      }))
      .then(async (controlIdentity) => {
        if (isCancelled()) {
          return
        }
        const { accountId, role } = controlIdentity
        // An account-admin Root key is scoped to its own account. Pin that
        // account as the assumed identity so admin and data calls target the
        // right tenant instead of failing with a mismatch (the server rejects
        // a foreign account with "ADMIN can only manage account: <x>"). A root
        // key is not account-scoped, so its account selection is left intact.
        if (
          role === 'admin' &&
          (!connection.apiKey ||
            connection.apiKey === connection.adminApiKey) &&
          accountId &&
          connection.accountId !== accountId
        ) {
          const next = synchronizeConnectionRuntime(
            { ...connection, accountId },
            serverMode,
          )
          synchronizedRoleProbeRef.current = {
            key: createConnectionRoleProbeKey(next, serverMode),
            role,
          }
          queryClient.clear()
          connectionGenerationRef.current += 1
          setConnection(next)
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
    connection.serviceSelection,
    connection.userId,
    queryClient,
    serverMode,
  ])

  React.useEffect(() => {
    const interceptorId = ovClient.instance.interceptors.response.use(
      (response) => response,
      (error) => {
        if (
          shouldRedirectToLoginOnApiError(error) &&
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

  const value = React.useMemo<AppConnectionContextValue>(() => {
    const sourceGeneration = connectionGenerationRef.current
    const sourceProfileId = profileState.activeId
    const assertSourceConnection = () => {
      if (
        sourceGeneration !== connectionGenerationRef.current ||
        sourceProfileId !== profilesRef.current?.activeId
      ) {
        throw connectionChangedError()
      }
    }

    const commitConnection = (
      next: ConnectionDraft,
      preserveIdentity = false,
    ) => {
      connectionGenerationRef.current += 1
      connectionRevisionRef.current += 1
      authPromptSuppressedUntilRef.current =
        Date.now() + AUTH_PROMPT_SUPPRESSION_MS
      const normalized = synchronizeConnectionRuntime(next, serverMode)
      if (preserveIdentity) {
        storeProfiles(activateIdentityProfile(profilesRef.current!, normalized))
      }
      queryClient.clear()
      setConnection(normalized)
    }

    const activateConnection = (next: ConnectionDraft) => {
      connectionGenerationRef.current += 1
      // Apply the new credentials before any new route effects can issue requests.
      const normalized = synchronizeConnectionRuntime(next, 'checking')
      reconnect()
      setGeneratedCredential(null)
      setConnection(normalized)
    }

    return {
      profiles: profileState.profiles,
      activeProfileId: profileState.activeId,
      saveProfile: (profile) => {
        if (!isValidConnectionUrl(profile.connection.baseUrl))
          throw new Error(i18n.t('studio:profiles.invalidUrl'))
        const normalized = {
          ...profile,
          name: profile.name.trim(),
          connection: normalizeConnectionDraft(
            normalizeProfileConnection(profile.connection),
          ),
        }
        const current = profilesRef.current!
        const exists = current.profiles.some((p) => p.id === profile.id)
        storeProfiles({
          ...current,
          profiles: exists
            ? current.profiles.map((p) =>
                p.id === profile.id ? normalized : p,
              )
            : [...current.profiles, normalized],
        })
        if (profile.id === current.activeId)
          activateConnection(normalized.connection)
      },
      activateProfile: (id) => {
        const current = profilesRef.current!
        const profile = current.profiles.find((p) => p.id === id)
        if (!profile || id === current.activeId) return
        storeProfiles({ ...current, activeId: id })
        activateConnection(profile.connection)
      },
      deleteProfile: (id) => {
        const current = profilesRef.current!
        if (id === current.activeId) return
        storeProfiles({
          ...current,
          profiles: current.profiles.filter((p) => p.id !== id),
        })
      },
      clearGeneratedCredential: () => setGeneratedCredential(null),
      connection,
      connectionRole,
      generatedCredential,
      identityScopeKey:
        profileState.activeId +
        '\u0000' +
        createIdentityScopeKey(connection, serverMode),
      isConnectionRoleLoading,
      isGatewayHost,
      openvikingUrl,
      openConnectionSettings,
      reconnect,
      serverHealth:
        serverHealth?.key ===
        createConnectionRoleProbeKey(connection, 'checking')
          ? serverHealth.data
          : null,
      saveConnection: (next) => {
        if (!isValidConnectionUrl(next.baseUrl))
          throw new Error(i18n.t('studio:profiles.invalidUrl'))
        commitConnection(normalizeProfileConnection(next))
      },
      setGeneratedCredential,
      serverMode,
      switchIdentity: async ({
        accountId,
        allowLegacyIdentityFallback,
        apiKey,
        userId,
      }) => {
        assertSourceConnection()
        if (serverMode === 'dev' || serverMode === 'checking') {
          throw createConnectionError('identitySwitchUnsupported')
        }

        const revision = ++connectionRevisionRef.current
        const assertCurrentConnection = () => {
          assertSourceConnection()
          if (revision !== connectionRevisionRef.current)
            throw connectionChangedError()
        }

        const requested = normalizeConnectionDraft({
          ...connection,
          accountId,
          apiKey: serverMode === 'trusted' ? '' : apiKey,
          userId,
        })
        const identity = await detectConnectionIdentity(requested, 'data')
        assertCurrentConnection()
        const resolvedIdentity = resolveSwitchedIdentity(
          requested,
          identity,
          allowLegacyIdentityFallback,
        )
        if (!resolvedIdentity) {
          throw createConnectionError('credentialMismatch')
        }

        if (pathname === '/directory' || pathname === '/memories') {
          await navigate({ replace: true, to: pathname })
        }
        if (pathname === '/filesystem') {
          await navigate({
            replace: true,
            search: { upload: false },
            to: '/filesystem',
          })
        }
        assertCurrentConnection()
        commitConnection(
          {
            ...requested,
            ...resolvedIdentity,
          },
          true,
        )
      },
      switchManagementAccount: async (accountId) => {
        assertSourceConnection()
        if (connectionRole !== 'root') {
          throw createConnectionError('rootRequired')
        }
        commitConnection(
          createManagementAccountConnection(
            connection,
            accountId,
            isGatewayHost,
          ),
          true,
        )
      },
    }
  }, [
    connection,
    profileState,
    storeProfiles,
    connectionRole,
    generatedCredential,
    isConnectionRoleLoading,
    isGatewayHost,
    openvikingUrl,
    navigate,
    openConnectionSettings,
    pathname,
    queryClient,
    reconnect,
    serverHealth,
    serverMode,
  ])

  return (
    <AppConnectionContext.Provider value={value}>
      {children}
    </AppConnectionContext.Provider>
  )
}
