import { ConnectionSettingsPage } from '#/features/connection/components/connection-settings-page'
import { GatewayConnectionSettings } from '#/features/connection/components/gateway-connection-settings'
import { useAppConnection } from '#/features/connection/hooks/use-app-connection'

export function ConnectionSettingsRoute() {
  const { isGatewayHost } = useAppConnection()
  return isGatewayHost ? (
    <GatewayConnectionSettings />
  ) : (
    <ConnectionSettingsPage />
  )
}
