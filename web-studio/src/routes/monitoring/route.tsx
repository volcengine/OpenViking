import { MonitoringRoute } from '#/features/workbench/monitoring/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/monitoring')({
  component: MonitoringRoute,
})
