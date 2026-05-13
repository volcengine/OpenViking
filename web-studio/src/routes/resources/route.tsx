import { createFileRoute, Outlet } from '@tanstack/react-router'

export const Route = createFileRoute('/resources')({
  validateSearch: (search: Record<string, unknown>) => ({
    ...(typeof search.uri === 'string' && { uri: search.uri }),
  }),
  component: ResourcesLayout,
})

function ResourcesLayout() {
  return <Outlet />
}
