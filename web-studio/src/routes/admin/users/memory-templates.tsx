import { MemoryTemplatesPage } from '#/features/admin/extraction-rules/components/memory-templates-page'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/users/memory-templates')({
  component: ScopedMemoryTemplatesPage,
})

function ScopedMemoryTemplatesPage() {
  const { identityScopeKey } = useAppConnection()
  return <MemoryTemplatesPage key={identityScopeKey} />
}
