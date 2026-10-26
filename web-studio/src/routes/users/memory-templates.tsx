import { createFileRoute } from '@tanstack/react-router'
import { MemoryTemplatesPage } from './-components/memory-templates-page'

export const Route = createFileRoute('/users/memory-templates')({
  component: MemoryTemplatesPage,
})
