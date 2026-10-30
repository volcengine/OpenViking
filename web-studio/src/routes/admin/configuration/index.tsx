import { createFileRoute } from '@tanstack/react-router'
import { OpenVikingConfigurationPage } from './-page'

export const Route = createFileRoute('/admin/configuration/')({
  component: OpenVikingConfigurationPage,
})
