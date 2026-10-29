import { isUsableServerHealth } from '#/lib/connection/health'
import { useAppConnection } from './use-app-connection'
import { resolveConfiguredServiceProvider } from '#/lib/studio-service'

// The connection provider owns health, auth mode and reconnection as one lifecycle.
export function useStudioService() {
  const { connection, serverHealth, serverMode } = useAppConnection()
  const ready =
    serverMode !== 'checking' &&
    serverMode !== 'offline' &&
    isUsableServerHealth(serverHealth)
  return {
    provider: ready
      ? resolveConfiguredServiceProvider(
          connection.baseUrl,
          connection.serviceSelection,
        )
      : ('unknown' as const),
    ready,
    isChecking: serverMode === 'checking',
    version:
      typeof serverHealth?.version === 'string' ? serverHealth.version : '',
  }
}
