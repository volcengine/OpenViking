import { createFileRoute } from '@tanstack/react-router'
import { useAppConnection } from '#/hooks/use-app-connection'
import { CompileForm } from './-components/compile-form'

export const Route = createFileRoute('/compile/new')({
  validateSearch: (
    s: Record<string, unknown>,
  ): { fromTask?: string; from?: string } => ({
    fromTask: typeof s.fromTask === 'string' ? s.fromTask : undefined,
    from:
      typeof s.from === 'string' && s.from.startsWith('viking://')
        ? s.from
        : undefined,
  }),
  component: NewCompile,
})
function NewCompile() {
  const { identityScopeKey } = useAppConnection()
  const { fromTask, from } = Route.useSearch()
  return (
    <CompileForm
      key={`${identityScopeKey}:${fromTask || ''}:${from || ''}`}
      fromTask={fromTask}
      sourceUri={from}
    />
  )
}
