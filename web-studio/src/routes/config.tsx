import { needsStudioSetup } from '#/features/connection/lib/profile-bootstrap'
import { createFileRoute, redirect } from '@tanstack/react-router'

// Compatibility for old bookmarks; configuration no longer lives here.
export const Route = createFileRoute('/config')({
  staticData: { studioPublic: true },
  beforeLoad: () => {
    throw redirect({
      to: needsStudioSetup() ? '/login' : '/settings',
      replace: true,
    })
  },
})
