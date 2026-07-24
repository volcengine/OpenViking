import { getHealth } from '#/lib/ov-client'

export type ServerAuthMode = 'api_key' | 'trusted' | 'dev'
export type ServerMode = ServerAuthMode | 'checking' | 'offline'

const SERVER_AUTH_MODES = new Set<ServerAuthMode>(['api_key', 'trusted', 'dev'])
const HEALTH_REQUEST_REUSE_MS = 1_000

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

function isServerAuthMode(value: unknown): value is ServerAuthMode {
  return (
    typeof value === 'string' && SERVER_AUTH_MODES.has(value as ServerAuthMode)
  )
}

export async function fetchServerHealth(
  baseUrl: string,
  headers: Record<string, string> = {},
): Promise<Record<string, unknown>> {
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl)
  if (!normalizedBaseUrl) {
    throw new Error('Missing server URL')
  }

  const key = JSON.stringify([
    normalizedBaseUrl,
    Object.entries(headers).sort(([left], [right]) =>
      left.localeCompare(right),
    ),
  ])
  const now = Date.now()
  if (recentHealthRequest?.key === key && recentHealthRequest.expiresAt > now) {
    return recentHealthRequest.promise
  }

  const promise = getHealth({
    baseURL: normalizedBaseUrl,
    headers: {
      Accept: 'application/json',
      ...headers,
    },
    throwOnError: true,
  }).then((response) => {
    const data = response.data
    return data && typeof data === 'object'
      ? (data as Record<string, unknown>)
      : {}
  })

  recentHealthRequest = {
    expiresAt: now + HEALTH_REQUEST_REUSE_MS,
    key,
    promise,
  }

  try {
    return await promise
  } catch (error) {
    if (recentHealthRequest.promise === promise) {
      recentHealthRequest = undefined
    }
    throw error
  }
}

export async function detectServerMode(
  baseUrl: string,
  headers?: Record<string, string>,
): Promise<ServerMode> {
  try {
    const data = await fetchServerHealth(baseUrl, headers)
    return isServerAuthMode(data.auth_mode) ? data.auth_mode : 'api_key'
  } catch {
    return 'offline'
  }
}
