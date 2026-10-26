import { useStudioService } from '#/hooks/use-studio-service'
import { Navigate, createFileRoute } from '@tanstack/react-router'

import { useAppConnection } from '#/hooks/use-app-connection'

export const Route = createFileRoute('/')({
  component: IndexRoute,
})

function IndexRoute() {
  const { provider } = useStudioService()
  const { isGatewayHost } = useAppConnection()
  return (
    <Navigate
      replace
      to={
        isGatewayHost
          ? '/gateway'
          : provider === 'opensource'
            ? '/home'
            : '/filesystem'
      }
    />
  )
}
