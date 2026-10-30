import { useAppConnection } from './use-app-connection'
import { useStudioService } from './use-studio-service'
import { canOpenManagement } from '#/lib/studio-mode'

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
    canManage:
      studioMode === 'workbench'
        ? provider === 'opensource' &&
          serverMode !== 'dev' &&
          Boolean(connection.adminApiKey.trim())
        : canOpenManagement({
            provider,
            connection,
            role: connectionRole,
            isRoleLoading: isConnectionRoleLoading,
            serverMode,
          }),
  }
}
