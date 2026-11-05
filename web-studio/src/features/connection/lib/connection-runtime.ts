import { createConnectionHealthHeaders } from '#/features/connection/lib/auth'
import {
  readCredentialStorage,
  writeCredentialStorage,
} from '#/features/connection/lib/credential-storage'
import type { ServerMode } from '#/features/connection/lib/health'
import {
  fetchServerHealth,
  normalizeBaseUrl,
  readServerIdentity,
  resolveServerAuthMode,
} from '#/features/connection/lib/health'
import type { ConnectionProfile } from '#/features/connection/lib/profiles'
import type { StudioMode } from '#/features/connection/lib/studio-mode'
import type {
  ConnectionDraft,
  ConnectionRole,
} from '#/features/connection/lib/types'
import i18n from '#/i18n'
import { fetchAdminAccounts } from '#/shared/api/admin'
import { resolveApiKeyAuth } from '#/shared/api/auth'
import { isOvClientError, ovClient } from '#/shared/api/ov-client/index'
import { identifyServiceProvider } from '#/shared/lib/studio-service'

export type {
  ConnectionDraft,
  ConnectionRole,
} from '#/features/connection/lib/types'

export { createConnectionHealthHeaders } from '#/features/connection/lib/auth'

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

export type AppConnectionContextValue = {
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
  studioMode: StudioMode
  switchIdentity: (identity: {
    accountId: string
    allowLegacyIdentityFallback?: boolean
    /** Keep the selected connection configuration while changing its scope. */
    preserveProfile?: boolean
    /** A management-only resource identity must not replace the user's workspace. */
    rememberWorkbench?: boolean
    signal?: AbortSignal
    apiKey: string
    userId: string
  }) => Promise<void>
  switchManagementAccount: (
    accountId: string,
    options?: { preserveProfile?: boolean },
  ) => Promise<void>
}

export const CONNECTION_STORAGE_KEY = 'ov_console_connection'

export const AUTH_PROMPT_SUPPRESSION_MS = 10000

export type ConnectionErrorKey =
  | 'credentialMismatch'
  | 'identitySwitchUnsupported'
  | 'rootRequired'

export function createConnectionError(key: ConnectionErrorKey): Error {
  return new Error(i18n.t(`connection:errors.${key}`))
}

export function connectionChangedError(): Error & {
  code: 'connectionChanged'
} {
  return Object.assign(new Error(i18n.t('studio:profiles.connectionChanged')), {
    code: 'connectionChanged' as const,
  })
}

export const ENV_BASE_URL =
  typeof import.meta.env.VITE_OV_BASE_URL === 'string'
    ? import.meta.env.VITE_OV_BASE_URL.trim()
    : ''

export const ENV_API_KEY =
  typeof import.meta.env.VITE_OV_API_KEY === 'string'
    ? import.meta.env.VITE_OV_API_KEY.trim()
    : ''

export const ENV_ADMIN_API_KEY =
  typeof import.meta.env.VITE_OV_ADMIN_API_KEY === 'string'
    ? import.meta.env.VITE_OV_ADMIN_API_KEY.trim()
    : ''

export const ENV_ACCOUNT =
  typeof import.meta.env.VITE_OV_ACCOUNT === 'string'
    ? import.meta.env.VITE_OV_ACCOUNT.trim()
    : ''

export const ENV_USER =
  typeof import.meta.env.VITE_OV_USER === 'string'
    ? import.meta.env.VITE_OV_USER.trim()
    : ''

export const DEFAULT_CONNECTION: ConnectionDraft = {
  accountId: ENV_ACCOUNT || 'default',
  adminApiKey: ENV_ADMIN_API_KEY,
  apiKey: ENV_API_KEY,
  baseUrl: ovClient.getOptions().baseUrl,
  userId: ENV_USER || 'default',
}

export function isBrowser(): boolean {
  return typeof window !== 'undefined'
}

export function isConnectionRole(value: unknown): value is ConnectionRole {
  return (
    value === 'root' ||
    value === 'admin' ||
    value === 'user' ||
    value === 'unknown'
  )
}

export function readStoredConnection(): Partial<ConnectionDraft> {
  if (!isBrowser()) {
    return {}
  }

  try {
    const raw = readCredentialStorage(CONNECTION_STORAGE_KEY)
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

export function persistConnection(connection: ConnectionDraft): void {
  if (!isBrowser()) {
    return
  }

  try {
    writeCredentialStorage(CONNECTION_STORAGE_KEY, JSON.stringify(connection))
  } catch {
    // Ignore localStorage failures in restricted environments.
  }
}

export function normalizeConnectionDraft(
  connection: ConnectionDraft,
): ConnectionDraft {
  return {
    authMode: connection.authMode,
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

export function hashSecret(value: string): string {
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

export function resolveIdentityField(
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

export function applyConnection(
  connection: ConnectionDraft,
  serverMode: ServerMode,
  studioMode: StudioMode = 'management',
): void {
  ovClient.setOptions({
    credentialScope: studioMode === 'management' ? 'control' : 'data',
    baseUrl: connection.baseUrl,
    apiKeyAuth: resolveApiKeyAuth(
      connection.baseUrl,
      connection.serviceSelection,
    ),
  })
  ovClient.setConnection({
    accountId: connection.accountId,
    adminApiKey:
      studioMode === 'management' || serverMode === 'trusted'
        ? connection.adminApiKey
        : '',
    apiKey: serverMode === 'trusted' ? '' : connection.apiKey,
    identityHeaders: serverMode === 'trusted' || serverMode === 'dev',
    trustedAuth: serverMode === 'trusted',
    userId: connection.userId,
  })
}

export function synchronizeConnectionRuntime(
  connection: ConnectionDraft,
  serverMode: ServerMode,
  studioMode: StudioMode = 'management',
): ConnectionDraft {
  const normalized = normalizeConnectionDraft(connection)
  // Keep the imperative request client ahead of the React tree. Identity
  // changes remount route content, whose child effects may start requests
  // before this provider's passive effects run.
  applyConnection(normalized, serverMode, studioMode)
  persistConnection(normalized)
  return normalized
}

export type ConnectionIdentity = {
  accountId: string
  role: ConnectionRole
  userId: string
}

export async function canListAccounts(
  connection: ConnectionDraft,
  signal?: AbortSignal,
): Promise<boolean> {
  if (
    !connection.adminApiKey ||
    identifyServiceProvider(connection.baseUrl) === 'volcengine' ||
    connection.serviceSelection === 'volcengine'
  ) {
    return false
  }

  try {
    await fetchAdminAccounts(
      {
        accountId: connection.accountId,
        apiKey: connection.adminApiKey,
        baseUrl: connection.baseUrl,
        userId: connection.userId,
      },
      signal,
    )
    return true
  } catch {
    return false
  }
}

export async function detectConnectionIdentity(
  connection: ConnectionDraft,
  credential: 'control' | 'data' = 'control',
  signal?: AbortSignal,
  serverMode?: ServerMode,
): Promise<ConnectionIdentity> {
  const data = await fetchServerHealth(
    connection.baseUrl,
    createConnectionHealthHeaders(connection, credential, serverMode),
    signal,
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
    healthRole !== 'root' &&
    resolveServerAuthMode(data) === 'trusted' &&
    (await canListAccounts(connection, signal))
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

export function readInitialConnection(): ConnectionDraft {
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
