import { getRouteApi } from '@tanstack/react-router'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { CompileForm } from '#/features/workbench/compile/components/compile-form'

export function NewCompile() {
  const { identityScopeKey } = useAppConnection()
  const { fromTask, from, target } = getRouteApi('/compile/new').useSearch()
  return (
    <CompileForm
      key={`${identityScopeKey}:${target || ''}:${fromTask || ''}:${from || ''}`}
      fromTask={target ? undefined : fromTask}
      sourceUri={target ? undefined : from}
      target={target}
    />
  )
}
