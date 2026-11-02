import type { ConnectionDraft } from './types'
import type { VerifiedConnection } from './connection-identity'
import { createVerifiedConnection } from './connection-identity'
import { StudioSetupError, StudioSetupRequestError } from './connection-errors'
import { assertAllowedConnection } from './connection-policy'
import { normalizeProfileConnection, isValidConnectionUrl } from './profiles'
import {
  fetchConnectionHealth,
  isUsableServerHealth,
  readServerIdentity,
  resolveServerAuthMode,
} from './health'
import { createConnectionHealthHeaders } from './auth'
import { resolveConnectionDataRoot } from './probe'
import { createClient } from '#/gen/ov-client/client'
import { getOvResult } from '#/shared/api/ov-client/index'
import i18n from '#/i18n/core'

export async function verifyConnection(
  draft: ConnectionDraft,
  signal: AbortSignal,
): Promise<VerifiedConnection> {
  const connection = normalizeProfileConnection({
    ...draft,
    baseUrl: draft.baseUrl.trim().replace(/\/+$/, ''),
    apiKey: draft.apiKey.trim(),
    adminApiKey: draft.adminApiKey.trim(),
  })
  assertAllowedConnection(connection)
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
  const checked = new Map([[verifiedKey, health]])
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
      checked.set(connection.apiKey, health)
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
  if (managementOnly && health.role === 'root') connection.userId = ''
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
  return createVerifiedConnection(connection, mode, {
    data: managementOnly ? undefined : health,
    // Trusted data assertions do not prove a control-plane role.
    control:
      mode === 'trusted' || !connection.adminApiKey
        ? undefined
        : checked.get(connection.adminApiKey),
  })
}

/** Compatibility for callers that only need a validated configuration. */
export async function verifyStudioSetup(
  draft: ConnectionDraft,
  signal: AbortSignal,
): Promise<ConnectionDraft> {
  return { ...(await verifyConnection(draft, signal)).configuration }
}
