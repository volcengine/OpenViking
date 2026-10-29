import { Navigate, createFileRoute } from '@tanstack/react-router'
import { useAppConnection } from '#/hooks/use-app-connection'
import { useStudioService } from '#/hooks/use-studio-service'

export const Route = createFileRoute('/memories')({
  component: MemoriesRedirect,
})
function MemoriesRedirect() {
  const { connection } = useAppConnection()
  const { provider } = useStudioService()
  const uri =
    provider === 'volcengine'
      ? 'viking://~/memories/'
      : `viking://user/${connection.userId}/memories/`
  return <Navigate replace to="/filesystem" search={{ uri, upload: false }} />
}
