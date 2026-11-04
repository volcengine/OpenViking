import type { ConnectionDraft } from './types'
import type { ServerMode } from '../studio-contract'

/** Select a credential scope for navigation; this does not grant authorization. */
export function resolveDataAccess(
  connection: ConnectionDraft,
  serverMode?: ServerMode,
) {
  return serverMode !== 'trusted' &&
    serverMode !== 'dev' &&
    !connection.apiKey &&
    Boolean(connection.adminApiKey)
    ? ('control-only' as const)
    : ('workbench' as const)
}

export function normalizeConnectionDraft(
  connection: ConnectionDraft,
): ConnectionDraft {
  return {
    authMode: connection.authMode,
    serviceSelection:
      connection.serviceSelection === 'opensource' ||
      connection.serviceSelection === 'volcengine'
        ? connection.serviceSelection
        : 'auto',
    accountId: connection.accountId.trim(),
    adminApiKey: connection.adminApiKey.trim(),
    apiKey: connection.apiKey.trim(),
    // Keep the URL as typed (whitespace trimmed only). Stripping the trailing
    // slash here ran on every keystroke and fought the input: typing the "//"
    // of "http://" kept collapsing back to "http:". Trailing slashes are
    // stripped where the URL is actually used instead (ovClient.setOptions,
    // fetchServerHealth, detectConnectionRole, and the admin client).
    baseUrl: connection.baseUrl.trim(),
    userId: connection.userId.trim(),
  }
}

export function isValidConnectionUrl(value: string): boolean {
  try {
    const url = new URL(value.trim())
    return (
      ['http:', 'https:'].includes(url.protocol) &&
      !url.username &&
      !url.password
    )
  } catch {
    return false
  }
}
