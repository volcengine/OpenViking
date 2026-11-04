import { MonitoringRoute } from '#/features/admin/monitoring/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/monitoring')({
  component: MonitoringRoute,
})
