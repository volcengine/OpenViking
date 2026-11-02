import { ServerConfigurationRoute } from '#/features/admin/server-configuration/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/configuration/server')({
  component: ServerConfigurationRoute,
})
