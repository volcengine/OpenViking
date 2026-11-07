import { BotManagementPage } from '#/features/admin/vikingbot/management-page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/vikingbot/')({
  component: BotManagementPage,
})
