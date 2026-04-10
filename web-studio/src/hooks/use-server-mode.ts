export type ServerMode = 'checking' | 'dev-implicit' | 'explicit-auth' | 'offline'

export type ServerModeBadge = {
  label: string
  variant: 'default' | 'secondary' | 'outline' | 'destructive'
}

export function normalizeBaseUrl(baseUrl: string): string {
  return baseUrl.trim().replace(/\/+$/, '')
}

export async function detectServerMode(baseUrl: string): Promise<ServerMode> {
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl)
  if (!normalizedBaseUrl) {
    return 'offline'
  }

  try {
    const response = await fetch(`${normalizedBaseUrl}/health`, {
      headers: {
        Accept: 'application/json',
      },
    })

    if (!response.ok) {
      return 'offline'
    }

    const data = await response.json() as { user_id?: string }
    return typeof data.user_id === 'string' && data.user_id.length > 0
      ? 'dev-implicit'
      : 'explicit-auth'
  } catch {
    return 'offline'
  }
}

export function describeServerMode(serverMode: ServerMode): ServerModeBadge {
  switch (serverMode) {
    case 'dev-implicit':
      return { label: '开发模式', variant: 'secondary' }
    case 'explicit-auth':
      return { label: '显式鉴权', variant: 'outline' }
    case 'offline':
      return { label: '未连接', variant: 'destructive' }
    default:
      return { label: '检测中', variant: 'outline' }
  }
}