import { useStudioMode } from '#/features/connection/hooks/use-studio-mode'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import {
  readPreferredMode,
  resolveStudioEntry,
} from '#/features/connection/lib/studio-mode'
import { Navigate } from '@tanstack/react-router'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'

export function IndexRoute() {
  const { provider } = useStudioService()
  const {
    isGatewayHost,
    connection,
    isConnectionRoleLoading,
    serverMode,
    activeProfileId,
  } = useAppConnection()
  const { canManage } = useStudioMode()
  if (isConnectionRoleLoading) return null
  return (
    <Navigate
      replace
      to={
        isGatewayHost
          ? '/admin/gateway'
          : resolveStudioEntry({
              canManage,
              connection,
              provider,
              preferredMode: readPreferredMode(connection, activeProfileId),
              serverMode,
            })
      }
    />
  )
}
