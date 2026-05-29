import { getHealth } from '#/lib/ov-client'

export type ServerAuthMode = 'api_key' | 'trusted' | 'dev'
export type ServerMode = ServerAuthMode | 'checking' | 'offline'

const SERVER_AUTH_MODES = new Set<ServerAuthMode>(['api_key', 'trusted', 'dev'])

export function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.trim().replace(/\/+$/, '')
}

function isServerAuthMode(value: unknown): value is ServerAuthMode {
  return typeof value === 'string' && SERVER_AUTH_MODES.has(value as ServerAuthMode)
}

export async function detectServerMode(baseUrl: string): Promise<ServerMode> {
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl)
  if (!normalizedBaseUrl) {
    return 'offline'
  }

  try {
    const response = await getHealth({
      baseURL: normalizedBaseUrl,
      headers: {
        Accept: 'application/json',
      },
      throwOnError: true,
    })

    const data = response.data as { auth_mode?: string }
    return isServerAuthMode(data.auth_mode) ? data.auth_mode : 'api_key'
  } catch {
    return 'offline'
  }
}
