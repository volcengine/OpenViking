import { createFileRoute } from '@tanstack/react-router'
import { useAppConnection } from '#/hooks/use-app-connection'
import { CompileForm } from './-components/compile-form'

export const Route = createFileRoute('/compile/new')({
  validateSearch: (s: Record<string, unknown>): { fromTask?: string } => ({
    fromTask: typeof s.fromTask === 'string' ? s.fromTask : undefined,
  }),
  component: NewCompile,
})
function NewCompile() {
  const { identityScopeKey } = useAppConnection()
  const { fromTask } = Route.useSearch()
  return (
    <CompileForm
      key={`${identityScopeKey}:${fromTask || ''}`}
      fromTask={fromTask}
    />
  )
}
