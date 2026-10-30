import { createFileRoute } from '@tanstack/react-router'
import { useAppConnection } from '#/hooks/use-app-connection'
import { ConnectionSettingsPage } from './-components/connection-settings-page'
import { GatewayConnectionSettings } from './-components/gateway-connection-settings'

export const Route = createFileRoute('/settings')({
  component: ConnectionSettingsRoute,
})

function ConnectionSettingsRoute() {
  const { isGatewayHost } = useAppConnection()
  return isGatewayHost ? (
    <GatewayConnectionSettings />
  ) : (
    <ConnectionSettingsPage />
  )
}
