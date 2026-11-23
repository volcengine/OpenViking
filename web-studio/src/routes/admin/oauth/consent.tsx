import { ConsentPage } from '#/features/oauth/consent-page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/admin/oauth/consent')({
  validateSearch: (search: Record<string, unknown>) => ({
    pending: typeof search.pending === 'string' ? search.pending : '',
  }),
  component: ConsentPage,
})
