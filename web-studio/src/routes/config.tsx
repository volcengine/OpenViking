import { needsStudioSetup } from '#/features/connection/lib/onboarding'
import { createFileRoute, redirect } from '@tanstack/react-router'

// Compatibility for old bookmarks; configuration no longer lives here.
export const Route = createFileRoute('/config')({
  beforeLoad: () => {
    throw redirect({
      to: needsStudioSetup() ? '/onboarding' : '/settings',
      replace: true,
    })
  },
})
