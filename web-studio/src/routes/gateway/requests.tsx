import { createFileRoute } from '@tanstack/react-router'

import { RequestsPage } from './-components/requests-page'
import { parseRequestsSearch } from './-lib/search'

export const Route = createFileRoute('/gateway/requests')({
  validateSearch: parseRequestsSearch,
  component: RequestsPage,
})
