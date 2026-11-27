import { getRouteApi } from '@tanstack/react-router'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { CompileForm } from '#/features/workbench/compile/components/compile-form'

export function NewCompile() {
  const { identityScopeKey } = useAppConnection()
  const { fromTask, from, name } = getRouteApi('/compile/new').useSearch()
  return (
    <CompileForm
      key={`${identityScopeKey}:${name || ''}:${fromTask || ''}:${from || ''}`}
      fromTask={name ? undefined : fromTask}
      sourceUri={name ? undefined : from}
      compileName={name}
    />
  )
}
