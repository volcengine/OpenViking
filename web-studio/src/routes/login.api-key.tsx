import { AuthEntry } from '#/app/composition/auth-entry'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/login/api-key')({
  staticData: { studioPublic: true, studioTheme: 'workbench' },
  component: () => <AuthEntry page="api-key" />,
})
