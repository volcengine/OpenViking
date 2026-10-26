import i18n from '#/i18n/core'
import { resolveConnectionDataRoot } from './probe'
import type { ConnectionDraft } from '#/lib/connection/types'
import { createConnectionHealthHeaders } from './auth'
import { createClient } from '#/gen/ov-client/client'
import { getOvResult, normalizeOvClientError } from '#/lib/ov-client'
import { createRandomUuid } from '#/lib/browser-crypto'
import {
  resolveServerAuthMode,
  fetchConnectionHealth,
  isUsableServerHealth,
  readServerIdentity,
} from './health'
import {
  isValidConnectionUrl,
  normalizeProfileConnection,
  PROFILES_STORAGE_KEY,
} from '#/lib/connection/profiles'

export const SETUP_STORAGE_KEY = 'ov_studio_setup'
export const WORKSPACE_SESSION_KEY = 'ov_studio_workspace_selected'
export class StudioSetupError extends Error {
  constructor(public readonly code: 'invalidService' | 'keyRequired') {
    super(code)
  }
}

export class StudioSetupRequestError extends Error {
  readonly stage: 'health' | 'data'
  readonly statusCode?: number

  constructor(
    stage: 'health' | 'data',
    cause: unknown,
    connection: ConnectionDraft,
  ) {
    const error = normalizeOvClientError(cause)
    let message = error.message
    for (const secret of [connection.apiKey, connection.adminApiKey]) {
      if (secret) message = message.replaceAll(secret, '[redacted]')
    }
    super(`${error.statusCode ? `HTTP ${error.statusCode}: ` : ''}${message}`, {
      cause,
    })
    this.stage = stage
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
      localStorage.getItem(PROFILES_STORAGE_KEY) || 'null',
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
      localStorage.getItem('ov_console_connection') || 'null',
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
  const health = await fetchConnectionHealth(connection, signal).catch(
    (error: unknown) => {
      throw new StudioSetupRequestError('health', error, connection)
    },
  )
  if (!isUsableServerHealth(health))
    throw new StudioSetupError('invalidService')
  const mode = resolveServerAuthMode(health)
  if (
    mode !== 'dev' &&
    !connection.apiKey &&
    !(connection.serviceSelection === 'opensource' && connection.adminApiKey)
  )
    throw new StudioSetupError('keyRequired')
  if (connection.serviceSelection === 'volcengine') {
    connection.accountId = ''
    connection.userId = ''
  }
  const identity = readServerIdentity(health)
  if (identity.accountId) connection.accountId = identity.accountId
  if (identity.userId) connection.userId = identity.userId
  const dataClient = createClient({
    baseURL: connection.baseUrl,
    headers: createConnectionHealthHeaders(connection, 'data'),
    timeout: 10000,
    throwOnError: true,
  })
  const managementOnly =
    mode === 'api_key' &&
    connection.serviceSelection === 'opensource' &&
    !connection.apiKey &&
    Boolean(connection.adminApiKey)
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
): '/filesystem' | '/users' | '/home' {
  if (connection.serviceSelection === 'volcengine') return '/filesystem'
  return !connection.apiKey && connection.adminApiKey ? '/users' : '/home'
}

export function completeStudioSetup(
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
  localStorage.setItem(
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
  localStorage.setItem('ov_console_connection', JSON.stringify(connection))
  localStorage.setItem(SETUP_STORAGE_KEY, 'complete')
  sessionStorage.setItem(WORKSPACE_SESSION_KEY, 'selected')
}
