import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { Navigate } from '@tanstack/react-router'

export function AdminIndex() {
  const { serverMode } = useAppConnection()
  return (
    <Navigate
      replace
      to={serverMode === 'dev' ? '/admin/configuration/server' : '/admin/users'}
    />
  )
}
