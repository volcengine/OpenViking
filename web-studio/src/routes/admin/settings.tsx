import { createFileRoute } from '@tanstack/react-router'
import { useAppConnection } from '#/hooks/use-app-connection'
import { GatewayConnectionSettings } from '#/routes/settings/route'
import { ConnectionSettingsPage } from '#/routes/settings/-components/connection-settings-page'

export const Route = createFileRoute('/admin/settings')({
  component: ManagementConnectionSettings,
})

function ManagementConnectionSettings() {
  const { isGatewayHost } = useAppConnection()
  return isGatewayHost ? (
    <GatewayConnectionSettings />
  ) : (
    <ConnectionSettingsPage />
  )
}
