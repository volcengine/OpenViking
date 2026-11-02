import type { ServiceSelection } from '#/platform/studio-contract'
import { resolveConfiguredServiceProvider } from '#/platform/connection/service-provider'

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
