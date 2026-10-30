import { createFileRoute } from '@tanstack/react-router'
import { MemoryTemplatesPage } from './-components/memory-templates-page'

export const Route = createFileRoute('/admin/users/memory-templates')({
  component: MemoryTemplatesPage,
})
