import { createFileRoute } from '@tanstack/react-router'

import { ContextGatewayLayout } from './-components/gateway-layout'

export const Route = createFileRoute('/context-gateway')({
  component: ContextGatewayLayout,
})
