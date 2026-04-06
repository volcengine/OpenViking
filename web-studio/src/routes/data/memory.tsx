import { createFileRoute } from '@tanstack/react-router'

import { MemoryPage } from '#/components/data/memory-page'

export const Route = createFileRoute('/data/memory')({
  component: MemoryPage,
})
