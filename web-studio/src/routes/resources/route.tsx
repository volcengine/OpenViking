import { createFileRoute, Outlet } from '@tanstack/react-router'

export const Route = createFileRoute('/resources')({
  component: ResourcesLayout,
})

function ResourcesLayout() {
  return <Outlet />
}
