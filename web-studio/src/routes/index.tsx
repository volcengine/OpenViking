import { useStudioMode } from '#/hooks/use-studio-mode'
import { readPreferredMode, resolveStudioEntry } from '#/lib/studio-mode'
import { useStudioService } from '#/hooks/use-studio-service'
import { Navigate, createFileRoute } from '@tanstack/react-router'

import { useAppConnection } from '#/hooks/use-app-connection'

export const Route = createFileRoute('/')({
  component: IndexRoute,
})

function IndexRoute() {
  const { provider } = useStudioService()
  const { isGatewayHost, connection, isConnectionRoleLoading } =
    useAppConnection()
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
              preferredMode: readPreferredMode(connection),
            })
      }
    />
  )
}
