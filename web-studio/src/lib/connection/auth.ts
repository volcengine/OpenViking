import type { ConnectionDraft } from './types'
import { resolveConfiguredServiceProvider } from '../studio-service'
import type { ServiceSelection } from '../studio-service'

export type ApiKeyAuth = 'header' | 'bearer'

export function resolveApiKeyAuth(
  baseUrl: string,
  selection: ServiceSelection = 'auto',
): ApiKeyAuth {
  return resolveConfiguredServiceProvider(baseUrl, selection) === 'volcengine'
    ? 'bearer'
    : 'header'
}

export function createConnectionHealthHeaders(
  connection: ConnectionDraft,
  credential: 'control' | 'data' = 'control',
): Record<string, string> {
  const headers: Record<string, string> = {}
  const auth = resolveApiKeyAuth(
    connection.baseUrl,
    connection.serviceSelection,
  )
  const apiKey =
    auth === 'bearer'
      ? connection.apiKey
      : credential === 'data'
        ? connection.apiKey || connection.adminApiKey
        : connection.adminApiKey || connection.apiKey
  if (!apiKey) return headers
  Object.assign(headers, createApiKeyHeaders(apiKey, auth))
  const isHosted = auth === 'bearer'
  if (!isHosted && connection.accountId) {
    headers['X-OpenViking-Account'] = connection.accountId
  }
  if (!isHosted && connection.userId) {
    headers['X-OpenViking-User'] = connection.userId
  }
  return headers
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
