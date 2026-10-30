import { createFileRoute } from '@tanstack/react-router'

import { GatewayLayout } from './-components/gateway-layout'

export const Route = createFileRoute('/admin/gateway')({
  component: GatewayLayout,
})
