import { MemoryTemplatesPage } from '#/features/admin/extraction-rules/components/memory-templates-page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/users/memory-templates')({
  component: MemoryTemplatesPage,
})
