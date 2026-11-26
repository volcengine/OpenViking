import { TasksRoute } from '#/features/workbench/tasks/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/tasks')({
  component: TasksRoute,
})
