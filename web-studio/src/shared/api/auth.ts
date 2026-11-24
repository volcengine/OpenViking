import type { ServiceSelection } from '#/shared/lib/studio-service'
import { resolveConfiguredServiceProvider } from '#/shared/lib/studio-service'

export type ApiKeyAuth = 'header' | 'bearer'

export function resolveApiKeyAuth(
  baseUrl: string,
  selection: ServiceSelection = 'auto',
): ApiKeyAuth {
  return resolveConfiguredServiceProvider(baseUrl, selection) === 'volcengine'
    ? 'bearer'
    : 'header'
}

export function createApiKeyHeaders(
  apiKey: string,
  auth: ApiKeyAuth,
): Record<string, string> {
  if (!apiKey.trim()) return {}
  return auth === 'bearer'
    ? { Authorization: `Bearer ${apiKey.trim()}` }
    : { 'X-API-Key': apiKey.trim() }
}
