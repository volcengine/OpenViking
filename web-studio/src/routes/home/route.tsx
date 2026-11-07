import { Navigate, createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/home')({
  component: () => <Navigate to="/filesystem" replace />,
})
