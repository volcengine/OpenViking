import { createFileRoute } from '@tanstack/react-router'
import { UserKeyPage } from './-user-key-page'

export const Route = createFileRoute('/connections/user-key')({
  component: UserKeyPage,
})
