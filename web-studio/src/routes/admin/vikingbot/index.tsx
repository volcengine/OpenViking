import { createFileRoute } from '@tanstack/react-router'
import { BotManagementPage } from './-management-page'

export const Route = createFileRoute('/admin/vikingbot/')({
  component: BotManagementPage,
})
