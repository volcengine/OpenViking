import type { ConnectionDraft } from '#/features/connection/lib/types'
import type { SessionHistorySource } from '#/features/workbench/sessions/lib/api'
import { resolveConfiguredServiceProvider } from '#/platform/connection/service-provider'

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
