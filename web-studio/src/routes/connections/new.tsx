import { createFileRoute } from '@tanstack/react-router'
import { ConnectionProfilesList } from '#/routes/settings/-components/connection-profiles'

export const Route = createFileRoute('/connections/new')({
  component: () => <ConnectionProfilesList editorId="new" />,
})
