import { createFileRoute, Outlet } from '@tanstack/react-router'

export const Route = createFileRoute('/sessions')({
  component: SessionsLayout,
})

function SessionsLayout() {
  return <Outlet />
}
