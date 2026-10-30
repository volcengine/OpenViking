import { createFileRoute } from '@tanstack/react-router'
import { ConnectionProfilesList } from '#/routes/settings/-components/connection-profiles'

export const Route = createFileRoute('/connections/$profileId')({
  component: Editor,
})
function Editor() {
  const { profileId } = Route.useParams()
  return <ConnectionProfilesList key={profileId} editorId={profileId} />
}
