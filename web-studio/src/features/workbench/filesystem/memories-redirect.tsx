import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { useStudioService } from '#/features/connection/hooks/use-studio-service'
import { Navigate } from '@tanstack/react-router'

export function MemoriesRedirect() {
  const { connection } = useAppConnection()
  const { provider } = useStudioService()
  const uri =
    provider === 'volcengine'
      ? 'viking://~/memories/'
      : `viking://user/${connection.userId}/memories/`
  return <Navigate replace to="/filesystem" search={{ uri, upload: false }} />
}
