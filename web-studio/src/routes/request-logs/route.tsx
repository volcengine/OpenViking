import { RequestLogsRoute } from '#/features/workbench/request-logs/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/request-logs')({
  component: RequestLogsRoute,
})
