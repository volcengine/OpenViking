import { createFileRoute, redirect } from '@tanstack/react-router'

// Preserve existing bookmarks without retaining a second copy of the page.
export const Route = createFileRoute('/playground')({
  beforeLoad: () => {
    throw redirect({
      to: '/filesystem',
      search: true,
      hash: true,
      replace: true,
    })
  },
})
