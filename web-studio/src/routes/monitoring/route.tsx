import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/monitoring')({
  beforeLoad: () => {
    throw redirect({ to: '/admin/monitoring', replace: true })
  },
})
