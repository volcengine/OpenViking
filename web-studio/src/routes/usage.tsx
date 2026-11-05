import { HomePage } from '#/features/workbench/home/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/usage')({
  component: HomePage,
})
