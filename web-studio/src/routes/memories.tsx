import { MemoriesRedirect } from '#/features/workbench/filesystem/memories-redirect'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/memories')({
  component: MemoriesRedirect,
})
