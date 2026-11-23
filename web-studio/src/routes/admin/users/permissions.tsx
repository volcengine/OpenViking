import { PermissionsPage } from '#/features/resource-acl/components/permissions-page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/users/permissions')({
  component: PermissionsPage,
})
