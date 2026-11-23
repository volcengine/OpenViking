import { Navigate, createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/directory')({
  component: () => <Navigate replace to="/filesystem" />,
})
