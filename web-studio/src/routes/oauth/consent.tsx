import { createFileRoute } from '@tanstack/react-router'
import { ConsentPage } from './-consent-page'

export const Route = createFileRoute('/oauth/consent')({
  validateSearch: (search: Record<string, unknown>) => ({
    pending: typeof search.pending === 'string' ? search.pending : '',
  }),
  component: ConsentPage,
})
