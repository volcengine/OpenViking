import { createFileRoute } from '@tanstack/react-router'
import { ConsentPage } from '#/routes/oauth/-consent-page'

export const Route = createFileRoute('/admin/oauth/consent')({
  validateSearch: (search: Record<string, unknown>) => ({
    pending: typeof search.pending === 'string' ? search.pending : '',
  }),
  component: ConsentPage,
})
