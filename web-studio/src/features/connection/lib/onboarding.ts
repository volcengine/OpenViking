import { createConnectionHealthHeaders } from '#/features/connection/lib/auth'
import {
  flushCredentialStorage,
  readCredentialStorage,
  writeCredentialStorage,
} from '#/features/connection/lib/credential-storage'
import {
  fetchConnectionHealth,
  isUsableServerHealth,
  readServerIdentity,
  resolveServerAuthMode,
} from '#/features/connection/lib/health'
import { resolveConnectionDataRoot } from '#/features/connection/lib/probe'
import {
  isValidConnectionUrl,
  normalizeProfileConnection,
  PROFILES_STORAGE_KEY,
} from '#/features/connection/lib/profiles'
import type { ConnectionDraft } from '#/features/connection/lib/types'
import {
  readPreferredMode,
  resolveStudioEntry,
} from '#/features/connection/lib/studio-mode'
import { createClient } from '#/gen/ov-client/client'
import i18n from '#/i18n/core'
import {
  getOvResult,
  normalizeOvClientError,
} from '#/shared/api/ov-client/index'
import { createRandomUuid } from '#/shared/lib/browser-crypto'

export const SETUP_STORAGE_KEY = 'ov_studio_setup'
export const WORKSPACE_SESSION_KEY = 'ov_studio_workspace_selected'
export class StudioSetupError extends Error {
  constructor(public readonly code: 'invalidService' | 'keyRequired') {
    super(code)
  }
}

export class StudioSetupRequestError extends Error {
  readonly stage: 'health' | 'data'
  readonly code: string
  readonly statusCode?: number

  constructor(
    stage: 'health' | 'data',
    cause: unknown,
    connection: ConnectionDraft,
  ) {
    const error = normalizeOvClientError(cause)
    let message = error.message
    for (const secret of [connection.apiKey, connection.adminApiKey].flatMap(
      (key) => [key, key.trim()],
    )) {
      if (secret) message = message.replaceAll(secret, '[redacted]')
    }
    super(`${error.statusCode ? `HTTP ${error.statusCode}: ` : ''}${message}`, {
      cause,
    })
    this.stage = stage
    this.code = error.code
    this.statusCode = error.statusCode
  }
}

export const CLOUD_SETUP_URL =
  'https://api.vikingdb.cn-beijing.volces.com/openviking'

function hasConnection(value: unknown, completed = false): boolean {
  if (!value || typeof value !== 'object') return false
  const c = value as Partial<ConnectionDraft>
  return (
    typeof c.baseUrl === 'string' &&
    isValidConnectionUrl(c.baseUrl) &&
    (completed ||
      Boolean(
        (typeof c.apiKey === 'string' && c.apiKey.trim()) ||
        (typeof c.adminApiKey === 'string' && c.adminApiKey.trim()),
      ) ||
      c.baseUrl.replace(/\/+$/, '') !== window.location.origin)
  )
}

export function needsStudioSetup(): boolean {
  try {
    // A new tab must reuse an existing connection, including pre-onboarding
    // installations. The session marker alone only survives a tab reload.
    return (
      sessionStorage.getItem(WORKSPACE_SESSION_KEY) !== 'selected' &&
      savedSetupConnections().length === 0
    )
  } catch {
    return true
  }
}

export function savedSetupConnections(): Array<{
  id: string
  name: string
  connection: ConnectionDraft
}> {
  try {
    const saved: unknown = JSON.parse(
      readCredentialStorage(PROFILES_STORAGE_KEY) || 'null',
    )
    if (
      saved &&
      typeof saved === 'object' &&
      'profiles' in saved &&
      Array.isArray(saved.profiles)
    )
      return saved.profiles.filter(
        (
          profile,
        ): profile is {
          id: string
          name: string
          connection: ConnectionDraft
        } =>
          profile &&
          typeof profile.id === 'string' &&
          typeof profile.name === 'string' &&
          hasConnection(profile.connection, true),
      )
    const legacy: unknown = JSON.parse(
      readCredentialStorage('ov_console_connection') || 'null',
    )
    if (
      hasConnection(
        legacy,
        localStorage.getItem(SETUP_STORAGE_KEY) === 'complete',
      )
    )
      return [
        {
          id: 'default',
          name: 'OpenViking',
          connection: normalizeProfileConnection(legacy as ConnectionDraft),
        },
      ]
  } catch {
    /* An invalid saved profile must not block a new connection. */
  }
  return []
}

export function markStudioSetupPending() {
  try {
    localStorage.setItem(SETUP_STORAGE_KEY, 'pending')
  } catch {
    /* Show the setup even without storage. */
  }
}

export async function verifyStudioSetup(
  draft: ConnectionDraft,
  signal: AbortSignal,
): Promise<ConnectionDraft> {
  const connection = normalizeProfileConnection({
    ...draft,
    baseUrl: draft.baseUrl.trim().replace(/\/+$/, ''),
    apiKey: draft.apiKey.trim(),
    adminApiKey: draft.adminApiKey.trim(),
  })
  if (!isValidConnectionUrl(connection.baseUrl))
    throw new Error(i18n.t('studio:errors.invalidServerUrl'))
  if (connection.serviceSelection === 'volcengine') connection.adminApiKey = ''
  let verifiedKey = connection.apiKey || connection.adminApiKey
  let health = await fetchConnectionHealth(
    connection,
    signal,
    connection.apiKey ? 'data' : 'control',
  ).catch(async (error: unknown) => {
    if (connection.apiKey && connection.adminApiKey) {
      try {
        const discovery = await fetchConnectionHealth(
          { ...connection, apiKey: '', adminApiKey: '' },
          signal,
          'data',
        )
        // Only server-confirmed trusted mode may replace a data key with Root.
        if (
          isUsableServerHealth(discovery) &&
          resolveServerAuthMode(discovery) === 'trusted'
        ) {
          const trusted = await fetchConnectionHealth(
            connection,
            signal,
            'control',
          )
          verifiedKey = connection.adminApiKey
          return trusted
        }
      } catch (cause) {
        throw new StudioSetupRequestError('health', cause, connection)
      }
    }
    throw new StudioSetupRequestError('health', error, connection)
  })
  if (!isUsableServerHealth(health))
    throw new StudioSetupError('invalidService')
  const mode = resolveServerAuthMode(health)
  connection.authMode = mode
  if (mode === 'trusted') {
    connection.adminApiKey = verifiedKey
    connection.apiKey = ''
    if (!connection.adminApiKey) throw new StudioSetupError('keyRequired')
  } else if (
    mode !== 'dev' &&
    !connection.apiKey &&
    !(connection.serviceSelection === 'opensource' && connection.adminApiKey)
  )
    throw new StudioSetupError('keyRequired')
  if (connection.serviceSelection === 'volcengine') {
    connection.accountId = ''
    connection.userId = ''
  }
  if (
    connection.serviceSelection === 'opensource' &&
    health.role === 'root' &&
    connection.apiKey
  ) {
    const rootKey = connection.apiKey
    const otherKey = connection.adminApiKey
    connection.adminApiKey = rootKey
    connection.apiKey = otherKey && otherKey !== rootKey ? otherKey : ''
    // Recognize reversed Root + User/Admin inputs without discarding either key.
    if (connection.apiKey) {
      health = await fetchConnectionHealth(connection, signal, 'data').catch(
        (error: unknown) => {
          throw new StudioSetupRequestError('health', error, connection)
        },
      )
      if (
        !isUsableServerHealth(health) ||
        (health.role !== 'admin' && health.role !== 'user')
      )
        throw new StudioSetupError('keyRequired')
    }
  } else if (
    connection.serviceSelection === 'opensource' &&
    health.role === 'admin'
  ) {
    connection.adminApiKey ||= connection.apiKey
    connection.apiKey ||= connection.adminApiKey
  }
  const managementOnly =
    mode === 'api_key' &&
    connection.serviceSelection === 'opensource' &&
    !connection.apiKey &&
    Boolean(connection.adminApiKey)
  const identity = readServerIdentity(health)
  // Control credentials do not identify a tenant data user. Root's default
  // identity is only a placeholder; an account-admin key binds the account,
  // but must not replace the user's selected data identity.
  if (!managementOnly) {
    if (identity.accountId) connection.accountId = identity.accountId
    if (identity.userId) connection.userId = identity.userId
  } else if (health.role === 'admin' && identity.accountId) {
    connection.accountId = identity.accountId
  }
  const dataClient = createClient({
    baseURL: connection.baseUrl,
    headers: createConnectionHealthHeaders(
      connection,
      managementOnly ? 'control' : 'data',
      mode,
    ),
    timeout: 10000,
    throwOnError: true,
  })
  if (managementOnly) {
    // ROOT cannot call tenant data APIs. Verify control access before letting
    // a new administrator open Users and mint a separate user credential.
    if (health.role !== 'root' && health.role !== 'admin')
      throw new StudioSetupError('keyRequired')
    await getOvResult(
      dataClient.get({
        url:
          health.role === 'root'
            ? '/api/v1/admin/accounts'
            : '/api/v1/admin/accounts/{account_id}/users',
        path: { account_id: connection.accountId },
        signal,
      }),
    ).catch((error: unknown) => {
      throw new StudioSetupRequestError('data', error, connection)
    })
  } else
    await getOvResult(
      dataClient.get({
        url: '/api/v1/fs/ls',
        query: {
          uri: resolveConnectionDataRoot(connection, 'root'),
          node_limit: 1,
          output: 'agent',
        },
        signal,
      }),
    ).catch((error: unknown) => {
      throw new StudioSetupRequestError('data', error, connection)
    })
  signal.throwIfAborted()
  return connection
}

export function studioSetupDestination(
  connection: ConnectionDraft,
): '/filesystem' | '/admin' | '/home' {
  if (connection.serviceSelection === 'volcengine') return '/filesystem'
  return resolveStudioEntry({
    connection,
    provider: 'opensource',
    canManage: Boolean(connection.adminApiKey),
    preferredMode: readPreferredMode(connection),
    serverMode: connection.authMode,
  })
}

export async function completeStudioSetup(
  connection: ConnectionDraft,
  name: string,
  selectedProfileId?: string,
) {
  // Do not enter the workspace if the browser cannot retain this connection.
  const saved = savedSetupConnections()
  const existing = selectedProfileId
    ? saved.find((profile) => profile.id === selectedProfileId)
    : undefined
  const id = existing?.id ?? createRandomUuid()
  writeCredentialStorage(
    PROFILES_STORAGE_KEY,
    JSON.stringify({
      activeId: id,
      profiles: existing
        ? saved.map((profile) =>
            profile.id === id ? { ...profile, connection } : profile,
          )
        : [...saved, { id, name, connection }],
    }),
  )
  writeCredentialStorage('ov_console_connection', JSON.stringify(connection))
  await flushCredentialStorage()
  localStorage.setItem(SETUP_STORAGE_KEY, 'complete')
  sessionStorage.setItem(WORKSPACE_SESSION_KEY, 'selected')
}
