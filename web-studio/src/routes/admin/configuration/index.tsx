import { OpenVikingConfigurationPage } from '#/features/admin/account-configuration/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/configuration/')({
  component: OpenVikingConfigurationPage,
})
