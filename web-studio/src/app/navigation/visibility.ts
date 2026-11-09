import type { ServerMode } from '#/features/connection/lib/health'

export function shouldShowGatewayNavigation({
  isGatewayHost,
  serverMode,
  allowed,
}: {
  isGatewayHost: boolean
  serverMode: ServerMode
  /** Gateway administration is available for the connected service. */
  allowed: boolean
}): boolean {
  return (
    isGatewayHost ||
    (serverMode !== 'checking' && serverMode !== 'offline' && allowed)
  )
}
