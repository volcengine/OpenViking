import { AdminMembershipTabs } from '#/app/navigation/admin-membership-tabs'

import { UserManagementPanel } from '#/features/admin/users/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/users/')({
  component: () => <UserManagementPanel navigation={<AdminMembershipTabs />} />,
})
