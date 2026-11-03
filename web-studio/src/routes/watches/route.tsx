import { WatchesRoute } from '#/features/workbench/watches/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/watches')({
  component: WatchesRoute,
})
