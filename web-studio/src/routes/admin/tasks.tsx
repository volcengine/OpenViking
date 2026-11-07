import { createFileRoute, redirect } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/tasks')({
  beforeLoad: () => {
    throw redirect({
      to: '/admin/monitoring',
      search: { tab: 'tasks' },
      replace: true,
    })
  },
})
