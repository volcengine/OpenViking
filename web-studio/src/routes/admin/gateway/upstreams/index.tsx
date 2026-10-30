import { createFileRoute } from '@tanstack/react-router'

import { UpstreamsPage } from '#/features/admin/gateway/components/upstreams-page'

export const Route = createFileRoute('/admin/gateway/upstreams/')({
  component: UpstreamsPage,
})
