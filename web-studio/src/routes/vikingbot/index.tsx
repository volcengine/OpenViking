import { createFileRoute } from '@tanstack/react-router'
import { VikingBotPage } from './-workbench-page'

export const Route = createFileRoute('/vikingbot/')({
  component: VikingBotPage,
})
