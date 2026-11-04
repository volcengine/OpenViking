import { ConnectionProfilesList } from '#/features/connection/components/connection-profiles'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/connections/new')({
  staticData: { studioTheme: 'workbench' },
  component: () => <ConnectionProfilesList editorId="new" />,
})
