import { createFileRoute } from '@tanstack/react-router'

import { GatewayLayout } from '#/features/admin/gateway/components/gateway-layout'

export const Route = createFileRoute('/admin/gateway')({
  component: GatewayLayout,
})
