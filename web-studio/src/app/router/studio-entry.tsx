import { resolveStudioEntry } from '#/platform/entry/resolve-entry'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { readPreferredMode } from '#/features/connection/lib/studio-mode'
import { Navigate } from '@tanstack/react-router'

import { useAppConnection } from '#/features/connection/hooks/use-app-connection'

export function IndexRoute() {
  const { provider } = useStudioService()
  const {
    isGatewayHost,
    connection,
    accessContext,
    isConnectionRoleLoading,
    activeProfileId,
  } = useAppConnection()
  if (isConnectionRoleLoading) return null
  return (
    <Navigate
      replace
      to={
        isGatewayHost
          ? '/admin/gateway'
          : resolveStudioEntry({
              access: accessContext.entryAccess,
              provider,
              preferredMode: readPreferredMode(connection, activeProfileId),
            })
      }
    />
  )
}
