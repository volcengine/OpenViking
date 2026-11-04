import { AuthEntry } from '#/app/composition/auth-entry'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/invite')({
  staticData: { studioPublic: true, studioTheme: 'workbench' },
  component: () => <AuthEntry page="invite" />,
})
