import { UserGroupsRoute } from '#/features/admin/groups/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/users/groups')({
  component: UserGroupsRoute,
})
