import { ConnectionSettingsRoute } from '#/features/connection/pages/settings-page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/settings')({
  component: ConnectionSettingsRoute,
})
