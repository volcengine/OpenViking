import { Navigate, createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/permissions')({
  component: () => <Navigate replace to="/admin/users/permissions" />,
})
