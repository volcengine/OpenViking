import { ConnectionProfilesList } from '#/features/connection/components/connection-profiles'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/connections/$profileId')({
  staticData: { studioTheme: 'workbench' },
  component: Editor,
})
function Editor() {
  const { profileId } = Route.useParams()
  return <ConnectionProfilesList key={profileId} editorId={profileId} />
}
