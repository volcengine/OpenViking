import { UserManagementPanel } from '#/features/admin/users/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/users/')({
  component: UserManagementPanel,
})
