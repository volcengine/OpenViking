import { getHealth } from '#/lib/ov-client'

export type BackendAuthMode = 'api_key' | 'trusted' | 'dev'

export type ServerMode = BackendAuthMode | 'checking' | 'offline'

export type ServerModeBadge = {
  labelKey: string
  variant: 'default' | 'secondary' | 'outline' | 'destructive'
}

export function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.trim().replace(/\/+$/, '')
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function resolveServerModeFromHealthData(data: unknown): ServerMode {
  if (!isRecord(data)) {
    return 'offline'
  }

  switch (data.auth_mode) {
    case 'api_key':
      return 'api_key'
    case 'dev':
      return 'dev'
    case 'trusted':
      return 'trusted'
    default:
      return 'offline'
  }
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

    return resolveServerModeFromHealthData(response.data)
  } catch {
    return 'offline'
  }
}

export function describeServerMode(serverMode: ServerMode): ServerModeBadge {
  switch (serverMode) {
    case 'api_key':
      return { labelKey: 'serverMode.apiKey', variant: 'outline' }
    case 'dev':
      return { labelKey: 'serverMode.dev', variant: 'secondary' }
    case 'offline':
      return { labelKey: 'serverMode.offline', variant: 'destructive' }
    case 'trusted':
      return { labelKey: 'serverMode.trusted', variant: 'outline' }
    default:
      return { labelKey: 'serverMode.checking', variant: 'outline' }
  }
}
