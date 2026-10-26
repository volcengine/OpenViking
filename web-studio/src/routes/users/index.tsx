import { createFileRoute } from '@tanstack/react-router'
import { UserManagementPanel } from './route'

export const Route = createFileRoute('/users/')({
  component: UserManagementPanel,
})
