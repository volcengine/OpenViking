import { getRouteApi } from '@tanstack/react-router'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'
import { CompileForm } from '#/features/workbench/compile/components/compile-form'

export function NewCompile() {
  const { identityScopeKey } = useAppConnection()
  const { fromTask, from, target, compileName } =
    getRouteApi('/compile/new').useSearch()
  const configId = compileName ?? target
  return (
    <CompileForm
      key={`${identityScopeKey}:${configId || ''}:${fromTask || ''}:${from || ''}`}
      fromTask={configId ? undefined : fromTask}
      sourceUri={configId ? undefined : from}
      target={target}
      compileName={compileName}
    />
  )
}
