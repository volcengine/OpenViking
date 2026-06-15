import { Navigate, createFileRoute } from '@tanstack/react-router'

import { useAppConnection } from '#/hooks/use-app-connection'

export const Route = createFileRoute('/')({
  component: IndexRoute,
})

function IndexRoute() {
  const { connectionRole, isConnectionRoleLoading } = useAppConnection()

  if (isConnectionRoleLoading) {
    return null
  }

  return (
    <Navigate
      replace
      to={
        connectionRole === 'admin' || connectionRole === 'root'
          ? '/home'
          : '/playground'
      }
    />
  )
}
