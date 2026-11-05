import { getRouteApi } from '@tanstack/react-router'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { CompileForm } from '#/features/workbench/compile/components/compile-form'

export function NewCompile() {
  const { identityScopeKey } = useAppConnection()
  const { fromTask, from, compileId } = getRouteApi('/compile/new').useSearch()
  return (
    <CompileForm
      key={`${identityScopeKey}:${compileId || ''}:${fromTask || ''}:${from || ''}`}
      fromTask={compileId ? undefined : fromTask}
      sourceUri={compileId ? undefined : from}
      compileId={compileId}
    />
  )
}
