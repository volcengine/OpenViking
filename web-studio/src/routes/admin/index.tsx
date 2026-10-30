import { Navigate, createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/')({ component: AdminIndex })

function AdminIndex() {
  return <Navigate replace to="/admin/users" />
}
