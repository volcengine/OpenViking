import { resolveConfiguredServiceProvider } from '../studio-service'
import type { ConnectionDraft } from '../connection/types'
import type { SessionHistorySource } from './api'

export function resolveSessionHistorySource(
  connection: ConnectionDraft,
): SessionHistorySource {
  return resolveConfiguredServiceProvider(
    connection.baseUrl,
    connection.serviceSelection,
  ) === 'volcengine'
    ? 'content'
    : 'api'
}
