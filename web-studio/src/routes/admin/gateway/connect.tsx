import { createFileRoute } from '@tanstack/react-router'

import { ConnectPage } from './-components/connect-page'
import { parseConnectSearch } from './-lib/search'

export const Route = createFileRoute('/admin/gateway/connect')({
  validateSearch: parseConnectSearch,
  component: ConnectPage,
})
