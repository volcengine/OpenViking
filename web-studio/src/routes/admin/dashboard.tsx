import { HomePage } from '#/features/workbench/home/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/dashboard')({
  component: () => <HomePage management />,
})
