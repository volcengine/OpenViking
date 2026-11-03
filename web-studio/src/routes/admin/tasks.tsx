import { TasksRoute } from '#/features/workbench/tasks/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/tasks')({
  component: () => <TasksRoute management />,
})
