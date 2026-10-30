import { createFileRoute } from '@tanstack/react-router'
import { PermissionsPage } from '#/routes/permissions/-components/permissions-page'

export const Route = createFileRoute('/admin/users/permissions')({
  component: PermissionsPage,
})
