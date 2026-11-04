import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { canShowManagementEntry } from '#/platform/access/management'

export function useStudioMode() {
  const { provider } = useStudioService()
  const {
    connection,
    connectionRole,
    isConnectionRoleLoading,
    serverMode,
    studioMode,
  } = useAppConnection()
  return {
    // This only exposes an entry. Management routes authenticate the control
    // credential after navigation; showing it must not trigger a Root probe.
    canManage: canShowManagementEntry({
      surface: studioMode,
      serviceProvider: provider,
      hasControlCredential: Boolean(connection.adminApiKey.trim()),
      role: connectionRole,
      isRoleLoading: isConnectionRoleLoading,
      serverMode,
    }),
  }
}
