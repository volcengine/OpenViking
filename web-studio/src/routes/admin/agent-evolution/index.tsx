import { createFileRoute, Navigate } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/agent-evolution/')({
  component: () => <Navigate replace to="/admin/configuration" />,
})
