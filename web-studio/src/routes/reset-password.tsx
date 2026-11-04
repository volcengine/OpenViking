import { AuthEntry } from '#/app/composition/auth-entry'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/reset-password')({
  staticData: { studioPublic: true, studioTheme: 'workbench' },
  component: () => <AuthEntry page="reset" />,
})
