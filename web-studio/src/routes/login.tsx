import { createFileRoute, Outlet } from '@tanstack/react-router'

export const Route = createFileRoute('/login')({
  staticData: { studioPublic: true, studioTheme: 'workbench' },
  component: Outlet,
})
