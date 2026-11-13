import { validateRetrievalSearch } from '#/features/workbench/retrieval/lib/search-state'
import { RetrievalPage } from '#/features/workbench/retrieval/page'
import { createFileRoute } from '@tanstack/react-router'

export const Route = createFileRoute('/retrieval')({
  validateSearch: validateRetrievalSearch,
  component: RetrievalPage,
})
