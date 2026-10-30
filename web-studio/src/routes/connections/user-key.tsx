import { UserKeyPage } from '#/features/connection/pages/-user-key-page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/connections/user-key')({
  component: UserKeyPage,
})
