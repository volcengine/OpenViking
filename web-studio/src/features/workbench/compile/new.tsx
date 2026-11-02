import { getRouteApi } from '@tanstack/react-router'
import { useConnectionAccess } from '#/features/connection/access'
import { CompileForm } from '#/features/workbench/compile/components/compile-form'

export function NewCompile() {
  const { identityScopeKey } = useConnectionAccess()
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
