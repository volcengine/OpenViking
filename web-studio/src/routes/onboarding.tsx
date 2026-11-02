import { createFileRoute, redirect } from '@tanstack/react-router'

// Keep first-use bookmarks working with the explicit sign-in route.
export const Route = createFileRoute('/onboarding')({
  staticData: { studioPublic: true },
  beforeLoad: () => {
    throw redirect({ to: '/login', replace: true })
  },
})
