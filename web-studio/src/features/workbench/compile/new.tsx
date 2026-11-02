import { getRouteApi } from '@tanstack/react-router'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { CompileForm } from '#/features/workbench/compile/components/compile-form'

export function NewCompile() {
  const { identityScopeKey } = useAppConnection()
  const { fromTask, from } = getRouteApi('/compile/new').useSearch()
  return (
    <CompileForm
      key={`${identityScopeKey}:${fromTask || ''}:${from || ''}`}
      fromTask={fromTask}
      sourceUri={from}
    />
  )
}
