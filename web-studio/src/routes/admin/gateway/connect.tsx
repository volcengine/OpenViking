import { createFileRoute } from '@tanstack/react-router'

import { ConnectPage } from '#/features/admin/gateway/components/connect-page'
import { parseConnectSearch } from '#/features/admin/gateway/lib/search'

export const Route = createFileRoute('/admin/gateway/connect')({
  validateSearch: parseConnectSearch,
  component: ConnectPage,
})
