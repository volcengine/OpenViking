import { createConnectionHealthHeaders } from '#/features/connection/lib/auth'
import type { ConnectionDraft } from '#/features/connection/lib/types'
import { createClient } from '#/gen/ov-client/client'
import i18n from '#/i18n/core'
import { getHealth } from '#/shared/api/ov-client/index'

import type { ServerAuthMode } from '#/shared/types/auth'

export type { ServerAuthMode, ServerMode } from '#/shared/types/auth'

const SERVER_AUTH_MODES = new Set<ServerAuthMode>([
  'api_key',
  'trusted',
  'dev',
  'oidc',
  'ldap',
])
const HEALTH_REQUEST_REUSE_MS = 1_000
const healthClient = createClient()

let recentHealthRequest:
  | {
      expiresAt: number
      key: string
      promise: Promise<Record<string, unknown>>
    }
  | undefined

export function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.trim().replace(/\/+$/, '')
}

export function isServerAuthMode(value: unknown): value is ServerAuthMode {
  return (
    typeof value === 'string' && SERVER_AUTH_MODES.has(value as ServerAuthMode)
  )
}

function forgetFailedHealthRequest(
  promise: Promise<Record<string, unknown>>,
): void {
  // Reconnection may clear or replace the cached request while this one is pending.
  if (recentHealthRequest?.promise === promise) recentHealthRequest = undefined
}

export async function fetchServerHealth(
  baseUrl: string,
  headers: Record<string, string> = {},
  signal?: AbortSignal,
): Promise<Record<string, unknown>> {
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl)
  if (!normalizedBaseUrl) {
    throw new Error(i18n.t('studio:errors.missingServerUrl'))
  }

  const key = JSON.stringify([
    normalizedBaseUrl,
    Object.entries(headers).sort(([left], [right]) =>
      left.localeCompare(right),
    ),
  ])
  const now = Date.now()
  if (
    !signal &&
    recentHealthRequest?.key === key &&
    recentHealthRequest.expiresAt > now
  ) {
    return recentHealthRequest.promise
  }

  const promise = getHealth({
    client: healthClient,
    baseURL: normalizedBaseUrl,
    headers: {
      Accept: 'application/json',
      ...headers,
    },
    timeout: 10000,
    throwOnError: true,
    signal,
  }).then((response) => {
    const data = response.data
    return data && typeof data === 'object'
      ? (data as Record<string, unknown>)
      : {}
  })

  if (!signal)
    recentHealthRequest = {
      expiresAt: now + HEALTH_REQUEST_REUSE_MS,
      key,
      promise,
    }

  try {
    return await promise
  } catch (error) {
    forgetFailedHealthRequest(promise)
    throw error
  }
}

export function resetServerHealthCache(): void {
  recentHealthRequest = undefined
}

export function resolveServerAuthMode(
  data: Record<string, unknown>,
): ServerAuthMode {
  return isServerAuthMode(data.auth_mode) ? data.auth_mode : 'api_key'
}

export function isUsableServerHealth(
  data: unknown,
): data is Record<string, unknown> {
  return Boolean(
    data &&
    typeof data === 'object' &&
    !Array.isArray(data) &&
    (data as Record<string, unknown>).status !== 'error' &&
    !(data as Record<string, unknown>).error &&
    (typeof (data as Record<string, unknown>).version === 'string' ||
      typeof (data as Record<string, unknown>).auth_mode === 'string'),
  )
}

export function readServerIdentity(health: Record<string, unknown>) {
  return {
    accountId: typeof health.account_id === 'string' ? health.account_id : '',
    userId: typeof health.user_id === 'string' ? health.user_id : '',
  }
}

/** Setup uses an isolated, cancellable request rather than the runtime reuse cache. */
export async function fetchConnectionHealth(
  connection: ConnectionDraft,
  signal: AbortSignal,
  credential: 'control' | 'data' = 'data',
) {
  const client = createClient({
    baseURL: normalizeBaseUrl(connection.baseUrl),
    headers: createConnectionHealthHeaders(connection, credential),
    timeout: 10000,
    throwOnError: true,
  })
  const response = await client.get({ url: '/health', signal })
  return response.data
}
