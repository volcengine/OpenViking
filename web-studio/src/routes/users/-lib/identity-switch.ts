import type { ServerMode } from '#/hooks/use-server-mode'

export function canSwitchToManagedUser({
  current,
  hasApiKey,
  serverMode,
}: {
  current: boolean
  hasApiKey: boolean
  serverMode: ServerMode
}): boolean {
  if (current) {
    return false
  }
  return serverMode === 'trusted' || hasApiKey
}
