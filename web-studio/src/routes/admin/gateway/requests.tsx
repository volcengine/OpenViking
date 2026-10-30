import { createFileRoute } from '@tanstack/react-router'

import { RequestsPage } from '#/features/admin/gateway/components/requests-page'
import { parseRequestsSearch } from '#/features/admin/gateway/lib/search'

export const Route = createFileRoute('/admin/gateway/requests')({
  validateSearch: parseRequestsSearch,
  component: RequestsPage,
})
