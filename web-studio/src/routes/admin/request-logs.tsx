import { RequestLogsRoute } from '#/features/workbench/request-logs/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/request-logs')({
  component: () => <RequestLogsRoute management />,
})
