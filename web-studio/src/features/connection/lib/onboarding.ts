// Legacy setup interface. Runtime consumers use verification and profile-bootstrap directly.
import type { ConnectionDraft } from './types'
import { resolveStudioEntry, readPreferredMode } from './studio-mode'
import { resolveConfiguredServiceProvider } from '#/platform/connection/service-provider'

export { StudioSetupError, StudioSetupRequestError } from './connection-errors'
export { verifyConnection, verifyStudioSetup } from './connection-verification'
export {
  SETUP_STORAGE_KEY,
  WORKSPACE_SESSION_KEY,
  needsStudioSetup,
  savedSetupConnections,
  markStudioSetupPending,
  completeStudioSetup,
} from './profile-bootstrap'

export const CLOUD_SETUP_URL =
  'https://api.vikingdb.cn-beijing.volces.com/openviking'

export function studioSetupDestination(
  connection: ConnectionDraft,
): '/filesystem' | '/admin' | '/home' {
  return resolveStudioEntry({
    connection,
    provider: resolveConfiguredServiceProvider(
      connection.baseUrl,
      connection.serviceSelection,
    ),
    canManage: Boolean(connection.adminApiKey),
    preferredMode:
      connection.authMode === 'api_key' ? null : readPreferredMode(connection),
    serverMode: connection.authMode,
  })
}
