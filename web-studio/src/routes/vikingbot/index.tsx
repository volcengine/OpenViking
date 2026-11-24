import { VikingBotPage } from '#/features/workbench/vikingbot/workbench-page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/vikingbot/')({
  component: VikingBotPage,
})
