import { createFileRoute } from '@tanstack/react-router'

import { OverviewPage } from './-components/overview-page'

export const Route = createFileRoute('/context-gateway/')({
  component: OverviewPage,
})
