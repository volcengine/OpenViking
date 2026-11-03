import { createFileRoute } from '@tanstack/react-router'

import { OverviewPage } from '#/features/admin/gateway/components/overview-page'

export const Route = createFileRoute('/admin/gateway/')({
  component: OverviewPage,
})
