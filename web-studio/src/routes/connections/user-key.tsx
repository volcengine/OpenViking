import { UserKeyPage } from '#/features/connection/pages/-user-key-page'
import { validateUserKeySearch } from '#/features/connection/lib/user-key'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/connections/user-key')({
  validateSearch: validateUserKeySearch,
  component: UserKeyPage,
})
